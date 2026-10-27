import Nav from "../../components/Nav";

// The frame around every page after sign-in: dark rail on the left, page on the right.
export default function ConsoleLayout({ children }) {
  return (
    <div className="app">
      <aside className="rail">
        <div className="brand">
          <div className="brand-name">Fraud Desk</div>
          <div className="brand-sub">Bank Fraud System</div>
        </div>
        <Nav />
      </aside>
      <main className="page">{children}</main>
    </div>
  );
}
