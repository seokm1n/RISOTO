import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";

import App from "./App";
import "./styles.css";

const router = createBrowserRouter([
  { path: "*", Component: App },
]);

// 개발 중 부작용을 조기에 발견하도록 StrictMode에서 최상위 앱을 마운트한다.
createRoot(document.getElementById("root")).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
