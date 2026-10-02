"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Dialog } from "@base-ui/react/dialog";
import {
  Boxes,
  BarChart3,
  CreditCard,
  KeyRound,
  BookOpen,
  Menu,
  X,
  Wallet,
  ChevronsUpDown,
  LogOut,
  Lock,
  Settings,
  ShieldCheck,
} from "lucide-react";
import { signOut } from "@/app/actions";
import { BALANCE_UNAVAILABLE } from "@/components/console-data-state";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

/** The page's main landmark id: the skip link's target (the console layout sets it on <main>). */
export const MAIN_ID = "console-main";

// 02-foundations: Models, API keys, Usage, Credits; then Docs and Settings; the operator area apart.
const NAV = [
  { href: "/models", label: "Models", icon: Boxes },
  { href: "/api-keys", label: "API keys", icon: KeyRound },
  { href: "/usage", label: "Usage", icon: BarChart3 },
  { href: "/billing", label: "Credits", icon: CreditCard },
];
const SECONDARY = [
  { href: "/docs", label: "Docs", icon: BookOpen },
  { href: "/settings", label: "Settings", icon: Settings },
];

const DESKTOP = "(min-width: 768px)";

type Account = {
  email: string;
  /** The exact available balance as display text, or `null` when the wallet could not be read. */
  balance: string | null;
  isOperator: boolean;
};

/**
 * The console navigation (UX-02, audit C9). Desktop: a persistent sidebar. Below 768px: a menu
 * button opening a modal drawer (Base UI Dialog, the WAI pattern): focus moves in and stays in, the
 * page behind is inert, Escape closes it and focus returns to the button. Closed, the drawer is not
 * in the document, so no offscreen link is in the tab order. It also closes on any route change
 * (it is open only for the path it was opened on) and when the window grows to desktop.
 */
export function Sidebar({ email, balance, isOperator }: Account) {
  const pathname = usePathname();
  const [openAt, setOpenAt] = useState<string | null>(null);
  const open = openAt === pathname;
  const close = () => setOpenAt(null);

  useEffect(() => {
    const desktop = window.matchMedia(DESKTOP);
    const onChange = () => {
      if (desktop.matches) setOpenAt(null);
    };
    desktop.addEventListener("change", onChange);
    return () => desktop.removeEventListener("change", onChange);
  }, []);

  const account = { email, balance, isOperator };
  return (
    <>
      <a
        href={`#${MAIN_ID}`}
        className="sr-only rounded-md bg-background px-3 py-2 text-sm font-medium focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[60] focus:ring-2 focus:ring-ring"
      >
        Skip to content
      </a>

      <header className="sticky top-0 z-30 flex h-[52px] items-center gap-2 border-b bg-background px-3 md:hidden">
        <Dialog.Root open={open} onOpenChange={(next) => setOpenAt(next ? pathname : null)}>
          <Dialog.Trigger render={<Button variant="ghost" size="icon-sm" aria-label="Open menu" />}>
            <Menu />
          </Dialog.Trigger>
          <Dialog.Portal>
            <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/50" />
            <Dialog.Popup className="fixed inset-y-0 left-0 z-50 flex w-60 max-w-[85vw] flex-col border-r bg-sidebar text-sidebar-foreground outline-none">
              <Dialog.Title className="sr-only">Navigation</Dialog.Title>
              <div className="flex h-12 items-center justify-between px-4">
                <Link href="/models" onClick={close} className="font-heading text-base font-semibold tracking-tight">
                  infrx
                </Link>
                <Dialog.Close render={<Button variant="ghost" size="icon-sm" aria-label="Close menu" />}>
                  <X />
                </Dialog.Close>
              </div>
              <Contents pathname={pathname} onNavigate={close} {...account} />
            </Dialog.Popup>
          </Dialog.Portal>
        </Dialog.Root>
        <span className="font-heading text-sm font-semibold tracking-tight">infrx</span>
      </header>

      <aside className="hidden w-60 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground md:flex">
        <div className="flex h-12 items-center px-4">
          <Link href="/models" className="font-heading text-base font-semibold tracking-tight">
            infrx
          </Link>
        </div>
        <Contents pathname={pathname} onNavigate={close} {...account} />
      </aside>
    </>
  );
}

function Contents({ pathname, onNavigate, email, balance, isOperator }: Account & { pathname: string; onNavigate: () => void }) {
  const link = ({ href, label, icon: Icon }: (typeof NAV)[number]) => (
    <NavLink key={href} href={href} pathname={pathname} onClick={onNavigate}>
      <Icon className="size-4" />
      {label}
    </NavLink>
  );
  return (
    <>
      <nav aria-label="Console" className="flex-1 space-y-0.5 overflow-y-auto px-2 py-2">
        {NAV.map(link)}
        <div className="my-2 border-t" />
        {SECONDARY.map(link)}
        {isOperator ? (
          <>
            <div className="my-2 border-t" />
            <NavLink href="/admin" pathname={pathname} onClick={onNavigate}>
              <ShieldCheck className="size-4" />
              Operator
            </NavLink>
          </>
        ) : null}
      </nav>

      <div className="space-y-2 border-t p-2">
        <Link
          href="/billing"
          onClick={onNavigate}
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-sidebar-accent"
        >
          <Wallet className="size-4 text-muted-foreground" />
          {balance === null ? (
            <span role="status" className="flex-1 text-muted-foreground">
              {BALANCE_UNAVAILABLE}
            </span>
          ) : (
            <>
              <span className="flex-1 text-muted-foreground">Credits</span>
              <span className="font-medium tabular-nums">{balance}</span>
            </>
          )}
        </Link>

        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <button className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-sidebar-accent" />
            }
          >
            <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-medium text-primary-foreground">
              {email.slice(0, 1).toUpperCase()}
            </span>
            <span className="min-w-0 flex-1 truncate">{email}</span>
            <ChevronsUpDown className="size-3.5 text-muted-foreground" />
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="start" className="w-56">
            <div className="px-1.5 py-1 text-xs break-all text-muted-foreground">{email}</div>
            <DropdownMenuSeparator />
            <DropdownMenuItem render={<Link href="/update-password" onClick={onNavigate} />}>
              <Lock className="size-4" />
              Change password
            </DropdownMenuItem>
            <form action={signOut}>
              <DropdownMenuItem render={<button type="submit" className="w-full" />} closeOnClick={false}>
                <LogOut className="size-4" />
                Sign out
              </DropdownMenuItem>
            </form>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </>
  );
}

function NavLink({
  href,
  pathname,
  onClick,
  children,
}: {
  href: string;
  pathname: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  const active = pathname === href || pathname.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground",
        active && "bg-sidebar-accent font-medium text-foreground",
      )}
    >
      {children}
    </Link>
  );
}
