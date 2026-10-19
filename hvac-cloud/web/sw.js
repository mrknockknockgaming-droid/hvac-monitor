/* Fullscope service worker: shows alert notifications sent by the server (hvaccloud/webpush.py) and opens
   the right page when one is tapped. It caches nothing; the app always loads fresh from the server. */
"use strict";

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener("push", function (e) {
  var m = {};
  try { m = e.data ? e.data.json() : {}; } catch (err) { m = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(m.title || "Fullscope", {
    body: m.body || "",
    tag: m.tag || undefined,
    renotify: !!m.tag,
    icon: "icons/icon-192.png",
    data: { url: m.url || self.registration.scope }
  }));
});

self.addEventListener("notificationclick", function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || self.registration.scope;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (wins) {
    for (var i = 0; i < wins.length; i++) {
      if (wins[i].url.indexOf(self.registration.scope) === 0 && "focus" in wins[i]) {
        return wins[i].navigate(url).then(function (w) { return (w || wins[i]).focus(); }).catch(function () { return wins[i].focus(); });
      }
    }
    return self.clients.openWindow(url);
  }));
});
