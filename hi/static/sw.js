/*
  Service worker for Web Push notifications.

  This file MUST be served from the site root (/sw.js) so its scope covers the
  whole site. app.py has a small route for that.
*/

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let payload = {
    title: "School bus update",
    body: "Open the app to see your bus.",
    url: "/student",
    tag: "bus-update",
  };

  if (event.data) {
    try {
      payload = Object.assign(payload, event.data.json());
    } catch (error) {
      payload.body = event.data.text();
    }
  }

  const options = {
    body: payload.body,
    icon: "/static/icons/bus-192.png",
    badge: "/static/icons/bus-badge.png",
    vibrate: [400, 150, 400, 150, 800],
    tag: payload.tag || "bus-update",
    renotify: true,
    requireInteraction: true,
    silent: false,
    data: { url: payload.url || "/student", tripType: payload.trip_type },
  };

  event.waitUntil(
    self.registration.showNotification(payload.title, options).then(() => {
      // Ask any open page to play an audible alert too.
      return self.clients.matchAll({ type: "window", includeUncontrolled: true });
    }).then((clients) => {
      clients.forEach((client) => client.postMessage({ type: "bus-approach", payload }));
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/student";

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(targetUrl);
      return null;
    })
  );
});
