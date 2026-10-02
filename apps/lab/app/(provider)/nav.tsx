"use client";
// UX-03: the provider shell's two client islands. NavLink marks the current page (aria-current);
// MobileNav is the <768px menu: UX-00's Drawer (modal, inert background, Escape, focus return),
// open only for the route it was opened on, so a navigation closes it (the App sidebar's openAt rule).
import { useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/dialog";
import s from "./operate.module.css";

export function NavLink({ href, children }: { href: string; children: ReactNode }) {
  const path = usePathname();
  const current = path === href || path.startsWith(`${href}/`);
  return (
    <Link href={href} className={s.link} aria-current={current ? "page" : undefined}>
      {children}
    </Link>
  );
}

export function MobileNav({ children }: { children: ReactNode }) {
  const path = usePathname();
  const [openAt, setOpenAt] = useState<string | null>(null);
  return (
    <div className={s.menu}>
      <Drawer
        title="infrx Lab"
        open={openAt === path}
        onOpenChange={(open) => setOpenAt(open ? path : null)}
        trigger={
          <Button variant="ghost" aria-label="Open navigation">
            <Menu aria-hidden /> Menu
          </Button>
        }
      >
        {children}
      </Drawer>
    </div>
  );
}
