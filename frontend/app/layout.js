import "./globals.css";
import Nav from "@/components/Nav";

export const metadata = {
  title: "AI Video Agency",
  description: "Autonomous multi-agent YouTube video production",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <div className="mx-auto flex min-h-screen max-w-7xl gap-6 px-6 py-6">
          <Nav />
          <main className="min-w-0 flex-1">{children}</main>
        </div>
      </body>
    </html>
  );
}
