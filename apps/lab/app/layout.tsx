import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./lab.css";

export const metadata: Metadata = { title: "infrx Lab", description: "Provider workspace for infrx models." };

// UX-00: the App's typefaces; lab.css scopes every rule to this root (components/ui/README.md).
const sans = Geist({ subsets: ["latin"], variable: "--font-sans" });
const mono = Geist_Mono({ subsets: ["latin"], variable: "--font-mono" });

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`lab ${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
