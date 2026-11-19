import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, AppWindow, BarChart3, Bell, Boxes, CalendarDays, Clapperboard, CloudCog, Coins, LayoutDashboard, LogOut,
  Menu, Moon, Send, Settings as SettingsIcon, Sparkles, Sun, Timer, Users,
} from "lucide-react";
import { api } from "@/lib/api";
import { useToasts } from "@/lib/hooks";
import { cn, fmtRelative } from "@/lib/utils";
import { Button, Spinner } from "@/components/ui";
import { AuthPage } from "@/pages/Auth";
import { SetupWizard } from "@/pages/SetupWizard";
import { Overview } from "@/pages/Overview";
import { Applications } from "@/pages/Applications";
import { ApplicationDetail } from "@/pages/ApplicationDetail";
import { Accounts } from "@/pages/Accounts";
import { CalendarPage } from "@/pages/Calendar";
import { Studio } from "@/pages/Studio";
import { Workflows } from "@/pages/Workflows";
import { Scheduler } from "@/pages/Scheduler";
import { Queue } from "@/pages/Queue";
import { Library } from "@/pages/Library";
import { Analytics } from "@/pages/Analytics";
import { Logs } from "@/pages/Logs";
import { Usage } from "@/pages/Usage";
import { SettingsPage } from "@/pages/Settings";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/applications", label: "Applications", icon: AppWindow },
  { to: "/accounts", label: "Social Accounts", icon: Users },
  { to: "/calendar", label: "Content Calendar", icon: CalendarDays },
  { to: "/studio", label: "AI Content Studio", icon: Sparkles },
  { to: "/workflows", label: "ComfyUI Workflows", icon: Boxes },
  { to: "/scheduler", label: "Automation Scheduler", icon: Timer },
  { to: "/queue", label: "Publishing Queue", icon: Send },
  { to: "/library", label: "Content Library", icon: Clapperboard },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
  { to: "/logs", label: "Execution Logs", icon: Activity },
  { to: "/usage", label: "AI Usage & Costs", icon: Coins },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

function ThemeToggle() {
  const [dark, setDark] = useState(document.documentElement.classList.contains("dark"));
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label="Toggle theme"
      onClick={() => {
        const next = !dark;
        document.documentElement.classList.toggle("dark", next);
        localStorage.setItem("theme", next ? "dark" : "light");
        setDark(next);
      }}
    >
      {dark ? <Sun /> : <Moon />}
    </Button>
  );
}

function Notifications() {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["notifications"], queryFn: () => api.get("/api/system/notifications"), refetchInterval: 15000 });
  const navigate = useNavigate();
  return (
    <div className="relative">
      <Button variant="ghost" size="icon" aria-label="Notifications" onClick={() => setOpen((o) => !o)}>
        <Bell />
        {data?.unread > 0 && (
          <span className="absolute right-1.5 top-1.5 flex size-4 items-center justify-center rounded-full bg-danger text-[10px] font-semibold text-white">
            {data.unread > 9 ? "9+" : data.unread}
          </span>
        )}
      </Button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-96 rounded-xl border bg-card shadow-xl">
          <div className="flex items-center justify-between border-b px-4 py-2.5">
            <span className="text-sm font-semibold">Notifications</span>
            <button
              className="text-xs text-primary"
              onClick={async () => {
                await api.post("/api/system/notifications/read-all");
                qc.invalidateQueries({ queryKey: ["notifications"] });
              }}
            >
              Mark all read
            </button>
          </div>
          <div className="max-h-96 overflow-y-auto scrollbar-thin">
            {(data?.items ?? []).length === 0 && <p className="p-4 text-sm text-muted-foreground">No notifications.</p>}
            {(data?.items ?? []).map((n: any) => (
              <button
                key={n.id}
                onClick={() => {
                  setOpen(false);
                  if (n.link) navigate(n.link);
                }}
                className={cn("block w-full border-b px-4 py-3 text-left hover:bg-muted", !n.read && "bg-primary/5")}
              >
                <div className="flex items-center gap-2">
                  <span className={cn("size-2 rounded-full", n.level === "error" ? "bg-danger" : n.level === "warning" ? "bg-warning" : "bg-primary")} />
                  <span className="text-sm font-medium">{n.title}</span>
                </div>
                {n.message && <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{n.message}</p>}
                <p className="mt-1 text-[11px] text-muted-foreground">{fmtRelative(n.created_at)}</p>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Toasts() {
  const list = useToasts();
  return (
    <div className="fixed bottom-4 right-4 z-[60] space-y-2">
      {list.map((t) => (
        <div
          key={t.id}
          className={cn(
            "max-w-sm rounded-lg border px-4 py-3 text-sm shadow-lg",
            t.tone === "error" ? "border-danger/40 bg-card text-danger" : t.tone === "success" ? "border-success/40 bg-card" : "bg-card",
          )}
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}

function Shell({ user, children }: { user: any; children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const qc = useQueryClient();
  useEffect(() => setMobileOpen(false), [location.pathname]);
  const sidebar = (
    <nav className="flex h-full flex-col">
      <div className="flex items-center gap-2.5 px-5 py-5">
        <img src="/favicon.svg" className="size-8" alt="" />
        <div>
          <p className="text-sm font-semibold leading-tight">AutoContent AI</p>
          <p className="text-[11px] text-muted-foreground">Local social media studio</p>
        </div>
      </div>
      <div className="flex-1 space-y-0.5 overflow-y-auto px-3 scrollbar-thin">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                isActive ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )
            }
          >
            <Icon className="size-4" />
            {label}
          </NavLink>
        ))}
      </div>
      <div className="border-t p-3">
        <div className="flex items-center justify-between rounded-lg px-2 py-1.5">
          <span className="text-sm text-muted-foreground">{user?.username}</span>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Sign out"
            onClick={async () => {
              await api.post("/api/auth/logout");
              qc.invalidateQueries({ queryKey: ["auth"] });
            }}
          >
            <LogOut />
          </Button>
        </div>
      </div>
    </nav>
  );
  return (
    <div className="flex h-full">
      <aside className="hidden w-64 shrink-0 border-r bg-sidebar lg:block">{sidebar}</aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 bg-black/40 lg:hidden" onClick={() => setMobileOpen(false)}>
          <aside className="h-full w-64 border-r bg-sidebar" onClick={(e) => e.stopPropagation()}>
            {sidebar}
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b bg-background/80 px-4 backdrop-blur lg:px-8">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Menu">
            <Menu />
          </Button>
          <div className="hidden items-center gap-2 text-xs text-muted-foreground lg:flex">
            <CloudCog className="size-4" /> Local worker
          </div>
          <div className="flex items-center gap-1">
            <Notifications />
            <ThemeToggle />
          </div>
        </header>
        <main className="flex-1 overflow-y-auto scrollbar-thin">
          <div className="mx-auto max-w-7xl px-4 py-6 lg:px-8">{children}</div>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const qc = useQueryClient();
  const { data: auth, isLoading } = useQuery({ queryKey: ["auth"], queryFn: () => api.get("/api/auth/status"), staleTime: 60_000 });
  const { data: sys } = useQuery({
    queryKey: ["system-status-light"],
    queryFn: () => api.get("/api/system/status"),
    enabled: !!auth?.authenticated,
    staleTime: 60_000,
  });
  useEffect(() => {
    const h = () => qc.invalidateQueries({ queryKey: ["auth"] });
    window.addEventListener("auth:expired", h);
    return () => window.removeEventListener("auth:expired", h);
  }, [qc]);
  if (isLoading)
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  if (!auth?.authenticated) return <AuthPage setup={auth?.setup_required} />;
  return (
    <>
      <Shell user={auth.user}>
        <Routes>
          <Route path="/" element={sys && !sys.setup_completed ? <Navigate to="/setup" /> : <Overview />} />
          <Route path="/setup" element={<SetupWizard />} />
          <Route path="/applications" element={<Applications />} />
          <Route path="/applications/:id" element={<ApplicationDetail />} />
          <Route path="/accounts" element={<Accounts />} />
          <Route path="/calendar" element={<CalendarPage />} />
          <Route path="/studio" element={<Studio />} />
          <Route path="/workflows" element={<Workflows />} />
          <Route path="/scheduler" element={<Scheduler />} />
          <Route path="/queue" element={<Queue />} />
          <Route path="/library" element={<Library />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/usage" element={<Usage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </Shell>
      <Toasts />
    </>
  );
}
