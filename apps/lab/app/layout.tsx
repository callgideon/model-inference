import type { Metadata } from "next";

export const metadata: Metadata = { title: "infrx Lab", description: "Provider workspace for infrx models." };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
