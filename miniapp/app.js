App({
  globalData: {
    conn: null, // {ip, port, token}
  },

  onLaunch() {
    this.loadConn();
  },

  loadConn() {
    try {
      this.globalData.conn = wx.getStorageSync("hub.conn") || null;
    } catch (e) {
      this.globalData.conn = null;
    }
    return this.globalData.conn;
  },

  saveConn(conn) {
    this.globalData.conn = conn;
    wx.setStorageSync("hub.conn", conn);
  },

  configured() {
    const c = this.globalData.conn || this.loadConn();
    return !!(c && c.ip && c.token);
  },

  baseUrl() {
    const c = this.globalData.conn;
    if (!c) return "";
    return `http://${c.ip}:${c.port || 8766}`;
  },

  // 统一请求：自动带 token，返回 Promise<data>
  request(path, options = {}) {
    const c = this.globalData.conn || this.loadConn();
    if (!c) return Promise.reject(new Error("未配对"));
    return new Promise((resolve, reject) => {
      wx.request({
        url: this.baseUrl() + path,
        method: options.method || "GET",
        data: options.data || {},
        header: {
          "X-Hub-Token": c.token,
          "Content-Type": "application/json",
        },
        success: (res) => {
          if (res.statusCode === 401) return reject(new Error("配对码无效"));
          if (res.statusCode >= 400) return reject(new Error((res.data && res.data.error) || `HTTP ${res.statusCode}`));
          resolve(res.data);
        },
        fail: (err) => reject(new Error("连不上电脑，确认同 Wi-Fi 且已开启伴随端")),
      });
    });
  },
});
