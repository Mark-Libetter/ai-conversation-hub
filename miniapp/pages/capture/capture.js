const app = getApp();

Page({
  data: { text: "", captures: [], error: "", saving: false, queued: 0 },

  onShow() {
    if (!app.configured()) {
      wx.switchTab({ url: "/pages/pair/pair" });
      return;
    }
    this.flushQueue().then(() => this.load());
  },

  onInput(e) {
    this.setData({ text: e.detail.value });
  },

  // 离线暂存：失败时入本地队列，联网后自动补发
  submit() {
    const text = this.data.text.trim();
    if (!text) return wx.showToast({ title: "先写点什么", icon: "none" });
    this.setData({ saving: true });
    app
      .request("/companion/capture", { method: "POST", data: { text, day: this.today() } })
      .then(() => {
        this.setData({ text: "", saving: false });
        wx.showToast({ title: "已记到 Hub", icon: "success" });
        this.load();
      })
      .catch(() => {
        const q = wx.getStorageSync("hub.captureQueue") || [];
        q.push({ text, day: this.today() });
        wx.setStorageSync("hub.captureQueue", q);
        this.setData({ text: "", saving: false, queued: q.length });
        wx.showToast({ title: "已暂存，联网后同步", icon: "none" });
      });
  },

  flushQueue() {
    const q = wx.getStorageSync("hub.captureQueue") || [];
    if (!q.length) return Promise.resolve();
    const chain = q.reduce(
      (acc, item) =>
        acc.then((kept) =>
          app
            .request("/companion/capture", { method: "POST", data: item })
            .then(() => kept)
            .catch(() => (kept.push(item), kept))
        ),
      Promise.resolve([])
    );
    return chain.then((kept) => {
      wx.setStorageSync("hub.captureQueue", kept);
      this.setData({ queued: kept.length });
    });
  },

  today() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  },

  load() {
    app
      .request("/companion/captures")
      .then((d) => this.setData({ captures: d.items || [], error: "" }))
      .catch((err) => this.setData({ error: err.message }));
  },
});
