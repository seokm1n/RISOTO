import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    // Docker/Windows 공유 볼륨에서도 파일 변경을 안정적으로 감지한다.
    watch: {
      usePolling: true,
      interval: 200,
    },
  },
});
