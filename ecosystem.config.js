// pm2 ile calistir:  pm2 start ecosystem.config.js  &&  pm2 save
// Windows'ta python yolu farkliysa "interpreter" alanini tam yola cevir ( or. "py").
module.exports = {
  apps: [
    {
      name: "whoop-stress-bot",
      script: "bot.py",
      interpreter: "python",
      cwd: __dirname,
      autorestart: true,
      max_restarts: 20,
    },
    {
      // Gunluk HR sync (her gun 05:30) - calisip cikar, restart yok
      name: "whoop-stress-sync",
      script: "sync.py",
      interpreter: "python",
      cwd: __dirname,
      autorestart: false,
      cron_restart: "30 5 * * *",
    },
    {
      // Haftalik rapor (Pazartesi 09:00) - Telegram'a gonderir
      name: "whoop-stress-report",
      script: "report.py",
      interpreter: "python",
      cwd: __dirname,
      autorestart: false,
      cron_restart: "0 9 * * 1",
    },
  ],
};
