/**
 * ContentIQ Chat API adapter
 * Connects the React frontend to the FastAPI backend at /chat.
 */

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "";

export interface Citation {
    document_title: string;
    page_number: number | null;
    slide_number: number | null;
    source_url: string;
    content_type: string;
    source_label: "INTERNAL" | "WEB";
    extracted_caption?: string | null;
}

export interface ChatResponse {
    answer: string;
    citations: Citation[];
    source_label: "INTERNAL" | "WEB";
    conversation_id: string;
    /** True when internal confidence is low — frontend should show a one-time web search consent prompt. */
    needs_web_permission?: boolean;
}

export interface ChatMessage {
    role: "user" | "assistant";
    content: string;
    citations?: Citation[];
    source_label?: "INTERNAL" | "WEB";
    timestamp: number;
    /** "permission_request" renders a one-time web search consent card instead of a normal answer. */
    type?: "permission_request";
}

/**
 * Send a chat message to the ContentIQ FastAPI backend.
 *
 * @param message           The user's query
 * @param conversationId    UUID maintained by the frontend across turns
 * @param webSearchEnabled  When true, backend skips internal search and queries Tavily.
 *                          Set either by the persistent toggle OR by one-time user consent.
 */
export async function sendChatMessage(
    message: string,
    conversationId: string,
    webSearchEnabled: boolean = false,
    webOnly: boolean = false,
): Promise<ChatResponse> {
    const response = await fetch(`${BACKEND_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            message,
            conversation_id: conversationId,
            web_search_enabled: webSearchEnabled,
            web_only: webOnly,
        }),
    });

    if (!response.ok) {
        let errorDetail = `HTTP ${response.status}`;
        try {
            const errorJson = await response.json();
            errorDetail = errorJson.detail || errorDetail;
        } catch {
            // ignore parse errors
        }
        throw new Error(`ContentIQ backend error: ${errorDetail}`);
    }

    return response.json() as Promise<ChatResponse>;
}

/**
 * Check that the backend is running.
 * Returns true if /health returns 200.
 */
export async function checkHealth(): Promise<boolean> {
    try {
        const response = await fetch(`${BACKEND_URL}/health`, { method: "GET" });
        return response.ok;
    } catch {
        return false;
    }
}
