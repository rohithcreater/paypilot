import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'PayPilot AI',
  description: 'Agentic checkout with trust checks, delivery tracking and dispute help.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
