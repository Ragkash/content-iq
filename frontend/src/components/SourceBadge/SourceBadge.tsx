import styles from "./SourceBadge.module.css";

interface SourceBadgeProps {
    label: "INTERNAL" | "WEB";
}

/**
 * SourceBadge — visually distinct pill badge indicating source type.
 *
 * [INTERNAL] → dark blue background, white text
 * [WEB]      → orange background, white text
 *
 * The PRD requires these to be immediately obvious — different colours,
 * clear labels, and shown prominently on every citation.
 */
export default function SourceBadge({ label }: SourceBadgeProps) {
    return (
        <span className={`${styles.badge} ${label === "WEB" ? styles.web : styles.internal}`}>
            {label}
        </span>
    );
}
