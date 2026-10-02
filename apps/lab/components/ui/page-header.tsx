import type { ReactNode } from "react";
import Link from "next/link";

export type Crumb = { href: string; label: string };

/** The page's identity: breadcrumb, the one h1, a one-line purpose, the action slot. Render it in
 * every state (loading, empty, failure) so a failure never replaces the page's identity. */
export function PageHeader({
  title,
  purpose,
  breadcrumb = [],
  actions,
}: {
  title: string;
  purpose?: ReactNode;
  breadcrumb?: Crumb[];
  actions?: ReactNode;
}) {
  return (
    <header className="lab-page-header">
      {breadcrumb.length > 0 ? (
        <nav aria-label="Breadcrumb">
          <ol className="lab-breadcrumb">
            {breadcrumb.map((c) => (
              <li key={c.href}>
                <Link href={c.href}>{c.label}</Link>
              </li>
            ))}
          </ol>
        </nav>
      ) : null}
      <div className="lab-page-header__row">
        <div>
          <h1 className="lab-page-header__title">{title}</h1>
          {purpose ? <p className="lab-page-header__purpose">{purpose}</p> : null}
        </div>
        {actions ? <div className="lab-page-header__actions">{actions}</div> : null}
      </div>
    </header>
  );
}
