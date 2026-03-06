import React from "react";
import ReactDOM from "react-dom/client";
import { createHashRouter, RouterProvider } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";

import "./index.css";

// ContentIQ v1: use our custom chat page directly
// The demo's full Chat.tsx (with auth, settings panel, etc.) is preserved
// and can be restored by swapping the import below.
import ContentIQChat from "./pages/chat/ContentIQChat";

/**
 * ContentIQ v1 routing:
 * "/" → ContentIQChat (no auth, no settings panel)
 *
 * v2: Add MSAL auth, import Chat from "./pages/chat/Chat" for full demo features
 */
const router = createHashRouter([
    {
        path: "/",
        element: <ContentIQChat />,
    },
    {
        path: "*",
        lazy: () => import("./pages/NoPage"),
    },
]);

const root = ReactDOM.createRoot(document.getElementById("root") as HTMLElement);

root.render(
    <React.StrictMode>
        <HelmetProvider>
            <RouterProvider router={router} />
        </HelmetProvider>
    </React.StrictMode>
);
