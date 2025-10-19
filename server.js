import express from "express";
import dotenv from "dotenv";
import path from "path";
import { fileURLToPath } from "url";
import open from "open";

// 初始化路径变量
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// 载入环境变量
dotenv.config();

// 创建 express 应用
const app = express();
const PORT = process.env.PORT || 3000;

// 让 public 文件夹可以被直接访问（前端文件放这里）
app.use(express.static(path.join(__dirname, "public")));

// ✅ 新增一个接口，向前端安全返回 key
app.get("/config", (req, res) => {
  res.json({
    GOOGLE_MAPS_API_KEY: process.env.GOOGLE_MAPS_API_KEY,
    OPENWEATHER_API_KEY: process.env.OPENWEATHER_API_KEY,
  });
});

// 启动服务器
app.listen(PORT, async () => {
  console.log(`✅ Server running at http://localhost:${PORT}/map.html`);
  await open(`http://localhost:${PORT}/map.html`);
});
