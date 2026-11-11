import type { Metadata } from "next";
import "./globals.css";
import Shell from "@/components/Shell";

export const metadata: Metadata = {
  title: "Expert Knowledge Extraction Platform",
  description: "KI-gestützte Experteninterviews zur Sicherung von Erfahrungswissen – LBS Hessen-Thüringen",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de" suppressHydrationWarning>
      <body suppressHydrationWarning>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
