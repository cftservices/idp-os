import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Industrial Data Platform',
  description: 'Live OPC-UA data — OPC-UA Simulator → MonsterMQ → MongoDB',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased overflow-hidden">{children}</body>
    </html>
  );
}
