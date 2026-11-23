/*
  ============================================================================
  School Bus Tracker - ESP32 + NEO-6M GPS firmware
  ============================================================================

  What it does
    1. Connects to the school Wi-Fi.
    2. Reads NMEA sentences from the NEO-6M over UART.
    3. Synchronises its clock with NTP (needed for TLS certificate checks).
    4. Waits for a "PAIR <code>" command on the Serial Monitor.
    5. Saves the device credential in ESP32 NVS (Preferences).
    6. Checks the server every 15 seconds whether a trip is active.
    7. Sends GPS every 5 seconds ONLY during an active trip and only with a
       valid GPS fix. If the fix is lost, it does not send fake coordinates.

  Libraries (Arduino IDE -> Library Manager)
    - TinyGPSPlus by Mikal Hart
    - ArduinoJson by Benoit Blanchon (version 7)
    (WiFi, HTTPClient, Preferences are included with the ESP32 board package.)

  Board: "ESP32 Dev Module" (or your exact board).
  Serial Monitor: 115200 baud, newline ending.

  Wiring (NEO-6M -> ESP32)
    NEO-6M VCC   -> 3.3V (some modules tolerate 5V; check your module)
    NEO-6M GND   -> GND
    NEO-6M TX    -> ESP32 GPIO16 (GPS RX)
    NEO-6M RX    -> ESP32 GPIO17 (GPS TX, optional but useful)

  IMPORTANT: this firmware validates the public HTTPS certificate. It does NOT
  disable certificate verification. Update server_root_ca.h if Cloudflare's
  certificate chain changes. See DETAIL.md.
  ============================================================================
*/

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <TinyGPSPlus.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <time.h>

#include "server_root_ca.h"

// ----------------------------- YOUR SETTINGS --------------------------------
// Replace these two values with the school Wi-Fi details. Keep this file
// private if your Wi-Fi password is sensitive.
const char *WIFI_SSID = "CHANGE_ME_WIFI_NAME";
const char *WIFI_PASSWORD = "CHANGE_ME_WIFI_PASSWORD";

// Your public Cloudflare Tunnel hostname (NO "https://", NO trailing slash).
const char *SERVER_HOST = "bus.example.com";
const int SERVER_PORT = 443;

// Endpoints (leave these unchanged).
const char *PAIR_PATH = "/api/esp32/pair";
const char *STATUS_PATH = "/api/esp32/status";
const char *GPS_PATH = "/api/esp32/gps";

// GPS serial pins on the ESP32.
const int GPS_RX_PIN = 16;
const int GPS_TX_PIN = 17;
const int GPS_BAUD = 9600;

// Timing.
const unsigned long GPS_SEND_INTERVAL_MS = 5000;
const unsigned long STATUS_POLL_INTERVAL_MS = 15000;
const unsigned long WIFI_RETRY_INTERVAL_MS = 10000;

// ---------------------------------------------------------------------------

TinyGPSPlus gps;
HardwareSerial gpsSerial(2);
Preferences preferences;
String deviceToken;
String hardwareId;
String serialLine;
String tripType;
bool activeTrip = false;
bool ntpReady = false;

unsigned long lastWifiAttempt = 0;
unsigned long lastStatusPoll = 0;
unsigned long lastGpsSend = 0;
unsigned long lastWeakWarning = 0;
unsigned long lastNtpSync = 0;

double lastLatitude = 0;
double lastLongitude = 0;
bool hasLastFix = false;
unsigned long lastFixAt = 0;

// ----------------------------- small helpers --------------------------------

bool clockIsValid() {
  return time(nullptr) > 1700000000;  // after November 2023
}

void printSeparator() {
  Serial.println("----------------------------------------------------------");
}

void feedGps() {
  while (gpsSerial.available() > 0) {
    gps.encode(gpsSerial.read());
  }
}

bool gpsHasFreshFix() {
  // isValid() alone is not enough: a fix from 10 minutes ago is not live.
  return gps.location.isValid() && gps.location.age() < 5000;
}

void rememberFix() {
  // Keep the latest fresh valid position. An expired fix is not a live fix.
  if (!gps.location.isValid() || gps.location.age() >= 5000) return;
  lastLatitude = gps.location.lat();
  lastLongitude = gps.location.lng();
  hasLastFix = true;
  lastFixAt = millis();
}

void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastWifiAttempt < WIFI_RETRY_INTERVAL_MS) return;

  lastWifiAttempt = millis();
  Serial.println("[WiFi] Connecting...");
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void synchronizeClock() {
  if (clockIsValid()) {
    ntpReady = true;
    return;
  }
  if (millis() - lastNtpSync < 30000 && lastNtpSync != 0) return;

  lastNtpSync = millis();
  Serial.println("[Time] Syncing clock with NTP (needed for HTTPS)...");
  configTime(0, 0, "pool.ntp.org", "time.cloudflare.com", "time.google.com");

  for (int i = 0; i < 20 && !clockIsValid(); i++) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (clockIsValid()) {
    ntpReady = true;
    Serial.println("[Time] Clock is ready.");
  } else {
    Serial.println("[Time] Clock not ready yet. Will retry.");
  }
}

// ----------------------------- HTTPS requests -------------------------------

/*
  Send one HTTPS request with full certificate validation.

  Returns the HTTP status code, or a negative value for a connection error.
  The server's response body is stored in responseBody.
*/
int httpsRequest(const String &path, const String &method, const String &body,
                 String &responseBody, bool needsAuth) {
  responseBody = "";

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[HTTPS] No Wi-Fi connection.");
    return -1;
  }
  if (!clockIsValid()) {
    Serial.println("[HTTPS] Clock is not valid; refusing TLS without time.");
    return -1;
  }

  WiFiClientSecure client;
  client.setCACert(ROOT_CA_PEM);  // certificate + hostname validation enabled
  client.setHandshakeTimeout(15);

  HTTPClient https;
  String url = String("https://") + SERVER_HOST + path;

  if (!https.begin(client, url)) {
    Serial.println("[HTTPS] Could not create request.");
    return -1;
  }
  https.setTimeout(15000);
  https.setReuse(false);
  https.addHeader("Content-Type", "application/json");
  if (needsAuth) {
    if (deviceToken.isEmpty()) {
      https.end();
      return -1;
    }
    https.addHeader("Authorization", "Bearer " + deviceToken);
  }

  int statusCode;
  if (method == "POST") {
    statusCode = https.POST(body);
  } else {
    statusCode = https.GET();
  }

  if (statusCode > 0) {
    responseBody = https.getString();
  }
  https.end();
  return statusCode;
}

String jsonError(const String &responseBody) {
  JsonDocument doc;
  if (deserializeJson(doc, responseBody) == DeserializationError::Ok) {
    const char *message = doc["error"] | "";
    if (strlen(message) > 0) return String(message);
  }
  return "Request failed.";
}

// ----------------------------- pairing --------------------------------------

void pairDevice(String code) {
  code.trim();
  code.toUpperCase();

  if (code.length() != 12) {
    Serial.println("[Pair] The code must be exactly 12 characters.");
    return;
  }
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[Pair] Connect to Wi-Fi first.");
    return;
  }
  if (!clockIsValid()) {
    Serial.println("[Pair] Clock is not ready. Wait a few seconds and retry.");
    return;
  }

  JsonDocument request;
  request["code"] = code;
  request["hardware_id"] = hardwareId;
  String body;
  serializeJson(request, body);

  Serial.println("[Pair] Sending pairing code to the server...");
  String response;
  int statusCode = httpsRequest(PAIR_PATH, "POST", body, response, false);

  if (statusCode == 200) {
    JsonDocument reply;
    DeserializationError error = deserializeJson(reply, response);
    const char *token = error ? "" : (reply["device_token"] | "");
    if (strlen(token) > 0) {
      deviceToken = String(token);
      preferences.putString("device_token", deviceToken);
      const char *busName = reply["bus_name"] | "bus";
      Serial.println("[Pair] SUCCESS - paired with " + String(busName) + ".");
      Serial.println("[Pair] Credential saved in ESP32 flash. Do not share it.");
      printSeparator();
      return;
    }
    Serial.println("[Pair] Server response did not contain a credential.");
  } else if (statusCode == 429) {
    Serial.println("[Pair] Too many attempts. Wait a few minutes.");
  } else if (statusCode > 0) {
    Serial.println("[Pair] Failed (HTTP " + String(statusCode) + "): " + jsonError(response));
  } else {
    Serial.println("[Pair] Connection failed. Check Wi-Fi, hostname and CA certificate.");
  }
}

// ----------------------------- device status --------------------------------

void pollTripStatus() {
  if (deviceToken.isEmpty()) return;

  String response;
  int statusCode = httpsRequest(STATUS_PATH, "GET", "", response, true);
  lastStatusPoll = millis();

  if (statusCode == 200) {
    JsonDocument doc;
    if (deserializeJson(doc, response) != DeserializationError::Ok) return;
    activeTrip = doc["active_trip"] | false;
    const char *typeFromServer = doc["trip_type"] | "";
    tripType = String(typeFromServer);
    if (activeTrip) {
      Serial.println("[Trip] Active trip: " + tripType + ". GPS uploads enabled.");
    }
  } else if (statusCode == 401) {
    Serial.println("[Server] Device credential rejected. Unpair it in the admin page and run PAIR again.");
    deviceToken = "";
    activeTrip = false;
    preferences.remove("device_token");
  } else if (statusCode > 0) {
    Serial.println("[Server] Status check HTTP " + String(statusCode) + ": " + jsonError(response));
  } else {
    Serial.println("[Server] Status check failed (network/TLS). Will retry.");
  }
}

// ----------------------------- GPS upload -----------------------------------

void sendGpsUpdate() {
  if (!activeTrip) return;

  feedGps();
  if (!gpsHasFreshFix()) {
    // Keep the last valid position internally; never upload an old or invalid
    // position as if it were live.
    if (millis() - lastWeakWarning > 30000) {
      lastWeakWarning = millis();
      Serial.println("[GPS] No valid fix. Waiting for satellites (Weak GPS Signal).");
    }
    return;
  }

  rememberFix();

  float speedMps = gps.speed.isValid() ? gps.speed.kmph() / 3.6f : 0.0f;
  if (speedMps < 0 || speedMps > 55) speedMps = 0;

  // NEO-6M does not report a direct accuracy in metres. HDOP is a useful
  // approximation; the server shows "Weak GPS" above 100 m.
  float accuracyMeters = 2500.0f;
  if (gps.hdop.isValid()) {
    accuracyMeters = gps.hdop.hdop() * 5.0f;
    if (accuracyMeters < 3) accuracyMeters = 3;
    if (accuracyMeters > 2500) accuracyMeters = 2500;
  }

  JsonDocument request;
  request["lat"] = gps.location.lat();
  request["lon"] = gps.location.lng();
  request["speed"] = speedMps;
  request["accuracy"] = accuracyMeters;
  if (gps.course.isValid()) request["heading"] = gps.course.deg();
  if (gps.satellites.isValid()) request["satellites"] = gps.satellites.value();

  String body;
  serializeJson(request, body);

  String response;
  int statusCode = httpsRequest(GPS_PATH, "POST", body, response, true);
  lastGpsSend = millis();

  if (statusCode == 200) {
    Serial.println("[GPS] Sent " + String(gps.location.lat(), 6) + ", " +
                   String(gps.location.lng(), 6) +
                   " | speed " + String(speedMps * 3.6f, 1) + " km/h" +
                   " | sats " + String(gps.satellites.value()));
  } else if (statusCode == 409) {
    activeTrip = false;
    Serial.println("[GPS] Server says no active trip. Uploads paused.");
    lastStatusPoll = 0;  // check again immediately
  } else if (statusCode == 401) {
    activeTrip = false;
    deviceToken = "";
    preferences.remove("device_token");
    Serial.println("[GPS] Credential rejected. Pair the device again.");
  } else if (statusCode == 429) {
    Serial.println("[GPS] Server is rate limiting. Waiting before retry.");
  } else if (statusCode > 0) {
    Serial.println("[GPS] HTTP " + String(statusCode) + ": " + jsonError(response));
  } else {
    Serial.println("[GPS] Upload failed (network/TLS). Will retry automatically.");
  }
}

// ----------------------------- serial commands ------------------------------

void printHelp() {
  printSeparator();
  Serial.println("Commands:");
  Serial.println("  PAIR A8K4P2X9M7QD   Pair this ESP32 using a code from the admin page");
  Serial.println("  STATUS              Show Wi-Fi, GPS, trip and credential state");
  Serial.println("  FORGET              Erase the device credential from this ESP32");
  Serial.println("  HELP                Show this help");
  printSeparator();
}

void printStatus() {
  printSeparator();
  Serial.println("Wi-Fi: " + String(WiFi.status() == WL_CONNECTED ? "connected" : "disconnected"));
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("IP: " + WiFi.localIP().toString());
    Serial.println("Signal: " + String(WiFi.RSSI()) + " dBm");
  }
  Serial.println("Clock: " + String(clockIsValid() ? "valid" : "not ready"));
  Serial.println("Credential: " + String(deviceToken.isEmpty() ? "NOT PAIRED" : "stored"));
  Serial.println("Active trip: " + String(activeTrip ? tripType : "none"));
  Serial.println("GPS valid: " + String(gps.location.isValid() ? "yes" : "no"));
  if (gps.location.isValid()) {
    Serial.println("Position: " + String(gps.location.lat(), 6) + ", " + String(gps.location.lng(), 6));
  }
  if (gps.satellites.isValid()) {
    Serial.println("Satellites: " + String(gps.satellites.value()));
  }
  if (hasLastFix) {
    Serial.println("Last valid fix was " + String((millis() - lastFixAt) / 1000) + " seconds ago.");
  }
  printSeparator();
}

void handleCommand(String line) {
  line.trim();
  if (line.isEmpty()) return;

  String upper = line;
  upper.toUpperCase();

  if (upper.startsWith("PAIR ")) {
    pairDevice(line.substring(5));
  } else if (upper == "STATUS") {
    printStatus();
  } else if (upper == "FORGET") {
    preferences.remove("device_token");
    deviceToken = "";
    activeTrip = false;
    Serial.println("[Pair] Credential erased from this ESP32.");
  } else if (upper == "HELP") {
    printHelp();
  } else {
    Serial.println("Unknown command. Type HELP.");
  }
}

void readSerialCommands() {
  while (Serial.available() > 0) {
    char character = (char)Serial.read();
    if (character == '\n' || character == '\r') {
      if (serialLine.length() > 0) {
        handleCommand(serialLine);
        serialLine = "";
      }
    } else if (serialLine.length() < 120) {
      serialLine += character;
    }
  }
}

// ----------------------------- setup / loop ---------------------------------

void setup() {
  Serial.begin(115200);
  delay(400);

  printSeparator();
  Serial.println("School Bus Tracker - ESP32 GPS");
  Serial.println("Firmware version 1.0");
  printSeparator();

  gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);

  preferences.begin("schoolbus", false);
  deviceToken = preferences.getString("device_token", "");
  Serial.println(deviceToken.isEmpty()
                   ? "Device is not paired. Generate a code in the admin page."
                   : "Device credential loaded from flash.");

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  // ESP32 MAC address is used only as a human-friendly hardware label.
  uint8_t mac[6];
  WiFi.macAddress(mac);
  char macText[18];
  snprintf(macText, sizeof(macText), "%02X:%02X:%02X:%02X:%02X:%02X",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  hardwareId = String(macText);

  Serial.println("Hardware ID: " + hardwareId);
  Serial.println("Server: https://" + String(SERVER_HOST));
  printHelp();
}

void loop() {
  feedGps();
  rememberFix();
  ensureWifi();

  if (WiFi.status() == WL_CONNECTED) {
    if (!clockIsValid()) {
      synchronizeClock();
    } else if (millis() - lastNtpSync > 6UL * 60UL * 60UL * 1000UL) {
      // Re-sync every six hours to keep TLS timestamps correct.
      lastNtpSync = millis();
      configTime(0, 0, "pool.ntp.org", "time.cloudflare.com");
    }
  }

  readSerialCommands();

  if (WiFi.status() == WL_CONNECTED && clockIsValid() && !deviceToken.isEmpty()) {
    if (lastStatusPoll == 0 || millis() - lastStatusPoll >= STATUS_POLL_INTERVAL_MS) {
      pollTripStatus();
    }

    if (activeTrip && millis() - lastGpsSend >= GPS_SEND_INTERVAL_MS) {
      sendGpsUpdate();
    }
  }

  delay(10);
}
