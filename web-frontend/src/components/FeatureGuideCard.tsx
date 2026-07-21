import { ChevronDown, CircleHelp } from "lucide-react";

type FeatureGuideCardProps = {
  title: string;
  description: string;
  items: string[];
  defaultOpen?: boolean;
};

export default function FeatureGuideCard({ title, description, items, defaultOpen = false }: FeatureGuideCardProps) {
  return (
    <details className="feature-guide-card" open={defaultOpen || undefined}>
      <summary>
        <span className="feature-guide-icon"><CircleHelp size={14} /></span>
        <span className="feature-guide-copy"><strong>{title}</strong><span>{description}</span></span>
        <ChevronDown className="feature-guide-chevron" size={14} />
      </summary>
      <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
    </details>
  );
}
