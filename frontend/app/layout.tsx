import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Javaab — The analyst in your pocket",
  description:
    "Privacy-first, executive-grade natural-language analytics. Clean messy data, understand your schema, join files in plain English, get answers — not jargon.",
};

export const viewport: Viewport = {
  themeColor: "#0B0B0C",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="grain vignette">{children}</body>
    </html>
  );
}
