const app = getApp();

Page({
  data: { loading: true, error: "", items: [], day: "" },

  onShow() {
    if (!app.configured()) {
      wx.switchTab({ url: "/pages/pair/pair" });
      return;
    }
    this.load();
  },

  load() {
    this.setData({ loading: true, error: "" });
    app
      .request("/companion/pending")
      .then((d) => this.setData({ loading: false, items: d.items || [], day: d.day || "" }))
      .catch((err) => this.setData({ loading: false, error: err.message }));
  },

  copy(e) {
    const text = e.currentTarget.dataset.text;
    wx.setClipboardData({ data: text, success: () => wx.showToast({ title: "已复制", icon: "success" }) });
  },
});
