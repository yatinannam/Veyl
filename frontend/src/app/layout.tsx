import type { Metadata } from "next";
import localFont from "next/font/local";

import { StartingUpNotice } from "@/components/starting-up-notice";

import "./globals.css";

// Font files come from pinned npm packages (SIL Open Font License), so builds never
// download from Google Fonts. next/font needs literal paths.
const sans = localFont({
  variable: "--font-sans",
  src: [
    { path: "../../node_modules/@fontsource/ibm-plex-sans/files/ibm-plex-sans-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "../../node_modules/@fontsource/ibm-plex-sans/files/ibm-plex-sans-latin-500-normal.woff2", weight: "500", style: "normal" },
    { path: "../../node_modules/@fontsource/ibm-plex-sans/files/ibm-plex-sans-latin-600-normal.woff2", weight: "600", style: "normal" },
  ],
});

const serif = localFont({
  variable: "--font-serif",
  src: "../../node_modules/@fontsource-variable/source-serif-4/files/source-serif-4-latin-wght-normal.woff2",
  weight: "200 900",
});

export const metadata: Metadata = {
  title: "Veyl",
  description: "Answers from your company's documents, limited to what you are allowed to read.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${sans.variable} ${serif.variable} h-full antialiased`}>
      <body className="min-h-full bg-background font-sans text-foreground">
        {children}
        <StartingUpNotice />
      </body>
    </html>
  );
}
