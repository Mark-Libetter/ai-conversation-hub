const app = getApp();

Page({
  data: {
    configured: false,
    loading: true,
    error: "",
    day: "",
    isToday: true,
    overview: "",
    stats: null,
    mainFocus: [],
    achievements: [],
    unfinished: [],
  },

  onShow() {
    if (!app.configured()) {
      wx.switchTab({ url: "/pages/pair/pair" });
      return;
    }
    this.setData({ configured: true });
    if (!this.data.day) this.data.day = this.today();
    this.load(this.data.day);
  },

  today() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  },

  shift(e) {
    const delta = Number(e.currentTarget.dataset.delta);
    const [y, m, d] = this.data.day.split("-").map(Number);
    const dt = new Date(y, m - 1, d + delta);
    const p = (n) => String(n).padStart(2, "0");
    const day = `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}`;
    if (day > this.today()) return;
    this.load(day);
  },

  load(day) {
    this.setData({ loading: true, error: "", day });
    app
      .request(`/companion/daily?day=${day}`)
      .then((d) => {
        this.setData({
          loading: false,
          isToday: d.is_today,
          overview: d.overview || "",
          stats: d.stats || null,
          mainFocus: d.main_focus || [],
          achievements: d.achievements || [],
          unfinished: d.unfinished || [],
        });
      })
      .catch((err) => {
        this.setData({ loading: false, error: err.message });
      });
  },
});
