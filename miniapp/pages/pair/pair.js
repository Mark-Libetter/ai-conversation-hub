const app = getApp();

Page({
  data: { ip: "", port: "8766", token: "", status: "", testing: false, connected: false },

  onShow() {
    const c = app.loadConn();
    if (c) this.setData({ ip: c.ip || "", port: c.port || "8766", token: c.token || "" });
  },

  set(e) {
    const field = e.currentTarget.dataset.field;
    this.setData({ [field]: e.detail.value });
  },

  save() {
    const conn = {
      ip: this.data.ip.trim(),
      port: Number(this.data.port) || 8766,
      token: this.data.token.trim(),
    };
    if (!conn.ip || !conn.token) {
      return wx.showToast({ title: "请填写 IP 与配对码", icon: "none" });
    }
    app.saveConn(conn);
    wx.showToast({ title: "已保存", icon: "success" });
  },

  test() {
    this.save();
    this.setData({ testing: true, status: "", connected: false });
    app
      .request("/companion/daily")
      .then(() => {
        this.setData({ testing: false, connected: true, status: "连接成功，可以开始使用。" });
      })
      .catch((err) => {
        this.setData({ testing: false, connected: false, status: `连接失败：${err.message}` });
      });
  },
});
