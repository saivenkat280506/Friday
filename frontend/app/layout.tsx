import type { Metadata } from "next";
import { Orbitron, JetBrains_Mono, Inter } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/Providers";

const orbitron = Orbitron({
  subsets: ["latin"],
  variable: "--font-orbitron",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
});

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: "F.R.I.D.A.Y.",
  description: "Female Replacement Intelligent Digital Assistant Youth",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${orbitron.variable} ${jetbrainsMono.variable} ${inter.variable} antialiased font-sans bg-background text-foreground overflow-hidden`}
      >
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  );
}
