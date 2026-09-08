self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(data.title || "Домашні контракти", {
      body: data.body || "У тебе новий контракт",
    })
  );
});
