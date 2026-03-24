import React, { useState, useRef, useEffect, useCallback } from "react";
import { v4 as uuidv4 } from "uuid";
import ReactMarkdown from "react-markdown";

import { sendChatMessage, ChatMessage, Citation } from "../../api/contentiqApi";
import CitationCard from "../../components/CitationCard/CitationCard";
import SourceBadge from "../../components/SourceBadge/SourceBadge";
import styles from "./ContentIQChat.module.css";

/**
 * ContentIQChat — main chat interface for the Content IQ agent.
 *
 * Two web search modes:
 *
 * Toggle OFF (default):
 *   - Queries hit internal documents only.
 *   - If confidence is low, a one-time permission card appears in chat.
 *   - User says "Yes" → that query is re-sent with web search, toggle stays OFF.
 *   - Next question goes back to internal search as normal.
 *
 * Toggle ON:
 *   - Every query goes straight to Tavily web search.
 */
export default function ContentIQChat() {
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [inputValue, setInputValue] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [webSearchEnabled, setWebSearchEnabled] = useState(false);
    // Holds the query that triggered a low-confidence result, waiting for consent.
    const [pendingWebQuery, setPendingWebQuery] = useState<string | null>(null);
    const conversationIdRef = useRef<string>(uuidv4());
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages, isLoading]);

    const sendMessage = useCallback(async () => {
        const trimmed = inputValue.trim();
        if (!trimmed || isLoading) return;

        setInputValue("");
        setError(null);

        setMessages(prev => [...prev, {
            role: "user",
            content: trimmed,
            timestamp: Date.now(),
        }]);
        setIsLoading(true);

        try {
            const response = await sendChatMessage(trimmed, conversationIdRef.current, webSearchEnabled);

            if (response.needs_web_permission) {
                // Internal docs didn't have enough — ask for one-time web consent
                setPendingWebQuery(trimmed);
                setMessages(prev => [...prev, {
                    role: "assistant",
                    content: "",
                    timestamp: Date.now(),
                    type: "permission_request",
                }]);
            } else {
                setMessages(prev => [...prev, {
                    role: "assistant",
                    content: response.answer,
                    citations: response.citations,
                    source_label: response.source_label,
                    timestamp: Date.now(),
                }]);
            }
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Unknown error");
        } finally {
            setIsLoading(false);
            setTimeout(() => inputRef.current?.focus(), 50);
        }
    }, [inputValue, isLoading, webSearchEnabled]);

    /**
     * User approved a one-time web search.
     * Re-send the pending query with web_search_enabled=true.
     * The toggle stays OFF — this is a single-query exception.
     */
    const handleWebSearchConsent = useCallback(async () => {
        if (!pendingWebQuery) return;
        const query = pendingWebQuery;
        setPendingWebQuery(null);
        setMessages(prev => prev.filter(m => m.type !== "permission_request"));
        setIsLoading(true);

        try {
            // web_only=true: internal search already failed, skip it and go straight to Tavily
            const response = await sendChatMessage(query, conversationIdRef.current, false, true);
            setMessages(prev => [...prev, {
                role: "assistant",
                content: response.answer,
                citations: response.citations,
                source_label: response.source_label,
                timestamp: Date.now(),
            }]);
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Unknown error");
        } finally {
            setIsLoading(false);
            setTimeout(() => inputRef.current?.focus(), 50);
        }
    }, [pendingWebQuery]);

    /** User declined — dismiss the permission card, toggle stays OFF. */
    const handleWebSearchDecline = () => {
        setPendingWebQuery(null);
        setMessages(prev => prev.filter(m => m.type !== "permission_request"));
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    };

    const clearChat = () => {
        setMessages([]);
        setError(null);
        setWebSearchEnabled(false);
        setPendingWebQuery(null);
        conversationIdRef.current = uuidv4();
        inputRef.current?.focus();
    };

    return (
        <div className={styles.container}>
            {/* ── Header ── */}
            <header className={styles.header}>
                <div className={styles.headerLeft}>
                    <span className={styles.logo}>🔍</span>
                    <div>
                        <h1 className={styles.title}>Content IQ</h1>
                        <p className={styles.subtitle}>Enterprise Document Intelligence</p>
                    </div>
                </div>

                <div className={styles.headerRight}>
                    <div className={styles.webToggle}>
                        <span className={`${styles.webToggleLabel} ${webSearchEnabled ? styles.webToggleLabelActive : ""}`}>
                            🌐 Web Search
                        </span>
                        <label
                            className={styles.toggleSwitch}
                            title={webSearchEnabled
                                ? "Web search ON — every query goes to Tavily"
                                : "Web search OFF — internal docs only (web used only with your approval)"}
                        >
                            <input
                                type="checkbox"
                                checked={webSearchEnabled}
                                onChange={e => setWebSearchEnabled(e.target.checked)}
                                aria-label="Toggle web search"
                            />
                            <span className={styles.toggleSlider} />
                        </label>
                    </div>

                    <button className={styles.clearBtn} onClick={clearChat} title="Start new conversation">
                        ↺ New Chat
                    </button>
                </div>
            </header>

            {/* ── Message history ── */}
            <main className={styles.messages} role="log" aria-live="polite" aria-label="Chat messages">
                {messages.length === 0 && !isLoading && (
                    <div className={styles.emptyState}>
                        <div className={styles.emptyIcon}>📂</div>
                        <h2>Ask anything about your documents</h2>
                        <p>Content IQ searches your internal documents and cites every answer. Enable Web Search to query the web.</p>
                        <div className={styles.exampleQueries}>
                            {[
                                "What have we presented to Shell recently?",
                                "What does the revenue chart in the Shell proposal show?",
                                "Who authored the Shell cloud migration proposal?",
                            ].map(q => (
                                <button
                                    key={q}
                                    className={styles.exampleChip}
                                    onClick={() => { setInputValue(q); inputRef.current?.focus(); }}
                                >
                                    {q}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {messages.map((msg, idx) => (
                    <div
                        key={idx}
                        className={`${styles.messageRow} ${msg.role === "user" ? styles.userRow : styles.assistantRow}`}
                    >
                        {msg.role === "user" ? (
                            <div className={styles.userBubble}>{msg.content}</div>
                        ) : msg.type === "permission_request" ? (
                            /* ── One-time Web Search Consent Card ── */
                            <div className={styles.assistantCard}>
                                <div className={styles.responseHeader}>
                                    <span className={styles.agentLabel}>Content IQ</span>
                                </div>
                                <div className={styles.permissionCard}>
                                    <p className={styles.permissionText}>
                                        I couldn't find enough information in the internal documents.
                                        Would you like me to search the web for this?
                                    </p>
                                    <div className={styles.permissionButtons}>
                                        <button className={styles.permissionYes} onClick={handleWebSearchConsent}>
                                            🌐 Yes, search the web
                                        </button>
                                        <button className={styles.permissionNo} onClick={handleWebSearchDecline}>
                                            No, thanks
                                        </button>
                                    </div>
                                </div>
                            </div>
                        ) : (
                            /* ── Normal Answer Card ── */
                            <div className={styles.assistantCard}>
                                <div className={styles.responseHeader}>
                                    <span className={styles.agentLabel}>Content IQ</span>
                                    {msg.source_label && <SourceBadge label={msg.source_label} />}
                                </div>

                                <div className={styles.answerBody}>
                                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                                </div>

                                {msg.citations && msg.citations.length > 0 && (
                                    <div className={styles.citations}>
                                        <p className={styles.citationsLabel}>
                                            Sources ({msg.citations.length})
                                        </p>
                                        <div className={styles.citationGrid}>
                                            {msg.citations.map((c: Citation, ci: number) => (
                                                <CitationCard key={ci} citation={c} />
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                ))}

                {isLoading && (
                    <div className={`${styles.messageRow} ${styles.assistantRow}`}>
                        <div className={styles.assistantCard}>
                            <div className={styles.responseHeader}>
                                <span className={styles.agentLabel}>Content IQ</span>
                            </div>
                            <div className={styles.typingIndicator} aria-label="Searching...">
                                <span />
                                <span />
                                <span />
                            </div>
                        </div>
                    </div>
                )}

                {error && (
                    <div className={styles.errorBanner} role="alert">
                        ⚠️ {error}
                        <button onClick={() => setError(null)} className={styles.dismissBtn}>✕</button>
                    </div>
                )}

                <div ref={messagesEndRef} />
            </main>

            {/* ── Input bar ── */}
            <footer className={styles.inputBar}>
                <textarea
                    ref={inputRef}
                    className={styles.input}
                    value={inputValue}
                    onChange={e => setInputValue(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={
                        webSearchEnabled
                            ? "Ask anything — web search is ON (Tavily)…"
                            : "Ask a question about your documents… (Enter to send, Shift+Enter for newline)"
                    }
                    rows={1}
                    disabled={isLoading}
                    aria-label="Message input"
                    autoFocus
                />
                <button
                    className={styles.sendBtn}
                    onClick={sendMessage}
                    disabled={isLoading || !inputValue.trim()}
                    aria-label="Send message"
                >
                    {isLoading ? (
                        <span className={styles.spinner} aria-hidden="true" />
                    ) : (
                        "↑"
                    )}
                </button>
            </footer>
        </div>
    );
}
