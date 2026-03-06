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
 * Layout:
 *   - Scrolling message history (user right, agent left)
 *   - Markdown-rendered agent answers
 *   - CitationCard grid below each agent response
 *   - Input bar pinned at bottom with send button
 *   - SourceBadge on each response header showing INTERNAL / WEB
 */
export default function ContentIQChat() {
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [inputValue, setInputValue] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const conversationIdRef = useRef<string>(uuidv4());
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);

    // Auto-scroll to bottom on new messages
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages, isLoading]);

    const sendMessage = useCallback(async () => {
        const trimmed = inputValue.trim();
        if (!trimmed || isLoading) return;

        setInputValue("");
        setError(null);

        // Add user message immediately
        const userMsg: ChatMessage = {
            role: "user",
            content: trimmed,
            timestamp: Date.now(),
        };
        setMessages(prev => [...prev, userMsg]);
        setIsLoading(true);

        try {
            const response = await sendChatMessage(trimmed, conversationIdRef.current);

            const assistantMsg: ChatMessage = {
                role: "assistant",
                content: response.answer,
                citations: response.citations,
                source_label: response.source_label,
                timestamp: Date.now(),
            };
            setMessages(prev => [...prev, assistantMsg]);
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : "Unknown error";
            setError(message);
        } finally {
            setIsLoading(false);
            // Restore focus to input
            setTimeout(() => inputRef.current?.focus(), 50);
        }
    }, [inputValue, isLoading]);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    };

    const clearChat = () => {
        setMessages([]);
        setError(null);
        conversationIdRef.current = uuidv4();  // new conversation
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
                <button className={styles.clearBtn} onClick={clearChat} title="Start new conversation">
                    ↺ New Chat
                </button>
            </header>

            {/* ── Message history ── */}
            <main className={styles.messages} role="log" aria-live="polite" aria-label="Chat messages">
                {messages.length === 0 && !isLoading && (
                    <div className={styles.emptyState}>
                        <div className={styles.emptyIcon}>📂</div>
                        <h2>Ask anything about your documents</h2>
                        <p>Content IQ searches your internal documents and cites every answer.</p>
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
                        ) : (
                            <div className={styles.assistantCard}>
                                {/* Response header with source badge */}
                                <div className={styles.responseHeader}>
                                    <span className={styles.agentLabel}>Content IQ</span>
                                    {msg.source_label && <SourceBadge label={msg.source_label} />}
                                </div>

                                {/* Markdown answer */}
                                <div className={styles.answerBody}>
                                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                                </div>

                                {/* Citations */}
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

                {/* Typing indicator */}
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

                {/* Error banner */}
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
                    placeholder="Ask a question about your documents… (Enter to send, Shift+Enter for newline)"
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
