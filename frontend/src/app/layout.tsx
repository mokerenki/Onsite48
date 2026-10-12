import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/components/AuthProvider";
import { ErrorBoundary } from "@/components/ErrorBoundary";

const inter = Inter({ 
  subsets: ["latin"],
  display: 'swap',
  variable: '--font-inter',
});

export const metadata: Metadata = {
  metadataBase: new URL("https://onsite48ai.world"),
  title: "Onsite48AI — Autonomous AI Workforce",
  description: "Secure, specialized autonomous agents for business workflows.",
  keywords: "autonomous agents, AI, enterprise, workflow automation, multi-agent",
  authors: [{ name: "onsite48AI" }],
  openGraph: {
    title: "onsite48AI - Autonomous Agent Platform",
    description: "Enterprise-grade autonomous agents for business workflows.",
    url: "https://onsite48ai.world",
    siteName: "onsite48AI",
    images: [
      {
        url: "/og-image.png",
        width: 1200,
        height: 630,
      },
    ],
    locale: "en_US",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "onsite48AI - Autonomous Agent Platform",
    description: "Enterprise-grade autonomous agents for business workflows.",
    images: ["/og-image.png"],
  },
  icons: {
    icon: "/favicon.ico",
    apple: "/apple-touch-icon.png",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="bg-onsite48-background text-onsite48-text antialiased">
        <ErrorBoundary>
          <AuthProvider>{children}</AuthProvider>
        </ErrorBoundary>
      </body>
    </html>
  );
}