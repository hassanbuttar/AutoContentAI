import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button, Card, ErrorBox, Field, Input } from "@/components/ui";

export function AuthPage({ setup }: { setup?: boolean }) {
  const qc = useQueryClient();
  const [username, setUsername] = useState(setup ? "admin" : "");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (setup && password !== confirm) return setError("Passwords do not match");
    setBusy(true);
    try {
      await api.post(setup ? "/api/auth/setup" : "/api/auth/login", { username, password });
      await qc.invalidateQueries({ queryKey: ["auth"] });
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex min-h-full items-center justify-center bg-gradient-to-br from-primary/10 via-background to-cyan-500/10 p-4">
      <Card className="w-full max-w-sm p-7">
        <div className="mb-6 flex items-center gap-3">
          <img src="/favicon.svg" className="size-10" alt="" />
          <div>
            <h1 className="text-lg font-semibold">AutoContent AI</h1>
            <p className="text-xs text-muted-foreground">{setup ? "Create the administrator account" : "Sign in to your local studio"}</p>
          </div>
        </div>
        <form onSubmit={submit} className="space-y-4">
          <Field label="Username">
            <Input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
          </Field>
          <Field label="Password" hint={setup ? "At least 10 characters." : undefined}>
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete={setup ? "new-password" : "current-password"} required minLength={setup ? 10 : 1} />
          </Field>
          {setup && (
            <Field label="Confirm password">
              <Input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required />
            </Field>
          )}
          <ErrorBox error={error} />
          <Button className="w-full" loading={busy} type="submit">
            {setup ? "Create account" : "Sign in"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
