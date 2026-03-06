/**
 * ContentIQ Chat API adapter
 * Connects the React frontend to the FastAPI backend at /chat.
 *
 * This replaces the demo's api.ts with a simpler, ContentIQ-specific
 * schema that maps to our { answer, citations, source_label } response.
 */

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:8000";

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
}

export interface ChatMessage {
    role: "user" | "assistant";
    content: string;
    citations?: Citation[];
    source_label?: "INTERNAL" | "WEB";
    timestamp: number;
}

/**
 * Send a chat message to the ContentIQ FastAPI backend.
 *
 * @param message         The user's query
 * @param conversationId  UUID maintained by the frontend across turns
 * @returns               ChatResponse with answer + citations + source_label
 */
export async function sendChatMessage(
    message: string,
    conversationId: string
): Promise<ChatResponse> {
    const response = await fetch(`${BACKEND_URL}/chat`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            message,
            conversation_id: conversationId,
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

    const data: ChatResponse = await response.json();
    return data;
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
