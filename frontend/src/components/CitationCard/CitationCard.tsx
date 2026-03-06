import styles from "./CitationCard.module.css";
import SourceBadge from "../SourceBadge/SourceBadge";

export interface Citation {
    document_title: string;
    page_number: number | null;
    slide_number: number | null;
    source_url: string;
    content_type: string;
    source_label: "INTERNAL" | "WEB";
    extracted_caption?: string | null;
}

interface CitationCardProps {
    citation: Citation;
}

/**
 * CitationCard — renders a single document citation below an agent response.
 *
 * - Clicking opens source_url in a new tab
 * - INTERNAL citations: blue badge + document icon
 * - WEB citations: orange badge + globe icon
 */
export default function CitationCard({ citation }: CitationCardProps) {
    const { document_title, page_number, slide_number, source_url, source_label, content_type } = citation;

    // Build location label: "Page 3" | "Slide 7" | ""
    let locationLabel = "";
    if (slide_number) {
        locationLabel = `Slide ${slide_number}`;
    } else if (page_number) {
        locationLabel = `Page ${page_number}`;
    }

    // Pick icon based on content type and source
    const icon =
        source_label === "WEB"
            ? "🌐"
            : content_type === "chart"
                ? "📊"
                : content_type === "table"
                    ? "📋"
                    : "📄";

    const handleClick = () => {
        if (source_url) {
            window.open(source_url, "_blank", "noopener,noreferrer");
        }
    };

    return (
        <div
            className={`${styles.card} ${source_label === "WEB" ? styles.web : styles.internal}`}
            onClick={handleClick}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === "Enter" && handleClick()}
            aria-label={`Open ${document_title} in new tab`}
        >
            <div className={styles.left}>
                <span className={styles.icon} aria-hidden="true">{icon}</span>
                <div className={styles.meta}>
                    <span className={styles.title}>{document_title}</span>
                    {locationLabel && (
                        <span className={styles.location}>{locationLabel}</span>
                    )}
                </div>
            </div>
            <div className={styles.right}>
                <SourceBadge label={source_label} />
                <span className={styles.externalIcon} aria-hidden="true">↗</span>
            </div>
        </div>
    );
}
