"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Boxes,
  BarChart3,
  Server,
  CreditCard,
  Users,
  KeyRound,
  BookOpen,
  ExternalLink,
  Menu,
  X,
  Wallet,
  ChevronsUpDown,
  LogOut,
  ShieldCheck,
} from "lucide-react";
import { signOut } from "@/app/actions";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/models", label: "Models", icon: Boxes },
  { href: "/usage", label: "Usage", icon: BarChart3 },
  { href: "/dedicated", label: "Dedicated", icon: Server },
  { href: "/billing", label: "Billing", icon: CreditCard },
  { href: "/teams", label: "Teams", icon: Users },
  { href: "/api-keys", label: "API Keys", icon: KeyRound },
];

export function Sidebar({
  email,
  balance,
  isOperator,
}: {
  email: string;
  balance: string;
  isOperator: boolean;
}) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  const close = () => setOpen(false);

  return (
    <>
      <header className="sticky top-0 z-30 flex h-12 items-center gap-2 border-b bg-background px-3 md:hidden">
        <Button variant="ghost" size="icon-sm" onClick={() => setOpen(true)} aria-label="Open menu">
          <Menu />
        </Button>
        <span className="font-heading text-sm font-semibold tracking-tight">infrx</span>
      </header>

      {open ? (
        <button
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          aria-label="Close menu"
          onClick={() => setOpen(false)}
        />
      ) : null}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-60 flex-col border-r bg-sidebar text-sidebar-foreground transition-transform md:static md:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-12 items-center justify-between px-4">
          <Link href="/models" className="font-heading text-base font-semibold tracking-tight">
            infrx
          </Link>
          <Button
            variant="ghost"
            size="icon-sm"
            className="md:hidden"
            onClick={() => setOpen(false)}
            aria-label="Close menu"
          >
            <X />
          </Button>
        </div>

        <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 py-2">
          {NAV.map(({ href, label, icon: Icon }) => (
            <NavLink key={href} href={href} active={pathname.startsWith(href)} onClick={close}>
              <Icon className="size-4" />
              {label}
            </NavLink>
          ))}
          {isOperator ? (
            <NavLink href="/admin" active={pathname.startsWith("/admin")} onClick={close}>
              <ShieldCheck className="size-4" />
              Admin
            </NavLink>
          ) : null}
        </nav>

        <div className="space-y-2 border-t p-2">
          <NavLink href="/docs" active={pathname.startsWith("/docs")} onClick={close}>
            <BookOpen className="size-4" />
            <span className="flex-1">Docs</span>
            <ExternalLink className="size-3.5 text-muted-foreground" />
          </NavLink>

          <Link
            href="/billing"
            onClick={close}
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-sidebar-accent"
          >
            <Wallet className="size-4 text-muted-foreground" />
            <span className="flex-1 text-muted-foreground">Balance</span>
            <span className="font-medium tabular-nums">{balance}</span>
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
              <div className="px-1.5 py-1 text-xs text-muted-foreground">{email}</div>
              <DropdownMenuSeparator />
              <form action={signOut}>
                <DropdownMenuItem
                  render={<button type="submit" className="w-full" />}
                  closeOnClick={false}
                >
                  <LogOut className="size-4" />
                  Sign out
                </DropdownMenuItem>
              </form>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </aside>
    </>
  );
}

function NavLink({
  href,
  active,
  onClick,
  children,
}: {
  href: string;
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      onClick={onClick}
      className={cn(
        "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground",
        active && "bg-sidebar-accent font-medium text-foreground",
      )}
    >
      {children}
    </Link>
  );
}
