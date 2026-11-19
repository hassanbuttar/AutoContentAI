import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Link2, Plug, Plus, RefreshCw, Trash2, Unplug, Users } from "lucide-react";
import { api } from "@/lib/api";
import { useAction, useApi, useToast } from "@/lib/hooks";
import { cn, fmtDate, fmtRelative, PLATFORM_COLOR, PLATFORM_LABEL } from "@/lib/utils";
import {
  Badge, Button, Card, Dialog, Empty, ErrorBox, Field, Input, PageHeader, Select, Spinner, StatusBadge, Switch,
} from "@/components/ui";

const PLATFORM_SETTINGS: Record<string, { key: string; label: string; options?: string[]; hint?: string }[]> = {
  youtube: [{ key: "privacy", label: "Privacy", options: ["public", "unlisted", "private"] }],
  instagram: [{ key: "variant", label: "Login variant", options: ["facebook_login", "instagram_login"],
    hint: "facebook_login uploads local files directly; instagram_login needs AUTOCONTENT_PUBLIC_BASE_URL" },
    { key: "ig_user_id", label: "Instagram user id (optional)", hint: "IG_USER_ID: picks this account when several are linked to your Pages" }],
  tiktok: [{ key: "post_mode", label: "Post mode", options: ["direct", "inbox"], hint: "inbox sends a draft to the TikTok app" },
    { key: "privacy_level", label: "Privacy", options: ["SELF_ONLY", "PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR"],
      hint: "Unaudited TikTok apps can only post SELF_ONLY" }],
  export: [],
};

function AccountCard({ a }: { a: any }) {
  const { toast } = useToast();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [s, setS] = useState<any>(a.settings ?? {});
  const [tokenOpen, setTokenOpen] = useState(false);
  const [token, setToken] = useState("");
  const inv = [["accounts"]];
  const tokenConnect = useAction((t: string) => api.post(`/api/accounts/${a.id}/token-connect`, { access_token: t }), inv, "Connected");
  const connect = async () => {
    try {
      const r = await api.post(`/api/accounts/${a.id}/oauth/start`);
      window.location.href = r.authorization_url;
    } catch (e: any) {
      toast(e.message, "error");
    }
  };
  const disconnect = useAction(() => api.post(`/api/accounts/${a.id}/disconnect`), inv, "Disconnected");
  const test = useAction(() => api.post(`/api/accounts/${a.id}/test`), [], undefined);
  const refresh = useAction(() => api.post(`/api/accounts/${a.id}/refresh`), inv, "Token refreshed");
  const patch = useAction((v: any) => api.patch(`/api/accounts/${a.id}`, v), inv, "Saved");
  const del = useAction(() => api.del(`/api/accounts/${a.id}`), inv, "Account removed");
  const isExport = a.platform === "export" || a.status === "export_only";
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className={cn("size-9 rounded-lg", PLATFORM_COLOR[a.platform])} />
          <div>
            <p className="font-semibold">{a.display_name}</p>
            <p className="text-xs text-muted-foreground">{PLATFORM_LABEL[a.platform]}{a.settings?.platform_display_name ? ` · ${a.settings.platform_display_name}` : ""}</p>
          </div>
        </div>
        <StatusBadge status={a.status} />
      </div>
      <div className="mt-4 space-y-1.5 text-xs text-muted-foreground">
        <p>Mode: <span className="text-foreground">{a.publishing_mode ?? "inherit from application"}</span>{a.paused && <Badge tone="warning" className="ml-2">paused</Badge>}</p>
        {a.oauth && <p>Token: {a.oauth.status} · expires {fmtRelative(a.oauth.expires_at)}{a.oauth.refresh_expires_at ? ` · reauth by ${fmtDate(a.oauth.refresh_expires_at)}` : ""}</p>}
        <p>Last published: {fmtRelative(a.last_published_at)}</p>
        {a.last_error && <p className="text-danger">{a.last_error}</p>}
      </div>
      {(a.capabilities?.notes ?? []).length > 0 && (
        <ul className="mt-3 list-disc space-y-1 pl-4 text-[11px] text-muted-foreground">
          {a.capabilities.notes.map((n: string) => <li key={n}>{n}</li>)}
        </ul>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        {!isExport && a.status !== "connected" && <Button size="sm" onClick={connect}><Plug /> Connect</Button>}
        {!isExport && a.platform === "instagram" && (
          <Button size="sm" variant="outline" onClick={() => setTokenOpen(true)}>Connect with token</Button>
        )}
        {!isExport && a.status === "connected" && <>
          <Button size="sm" variant="outline" loading={test.isPending} onClick={async () => {
            const r: any = await test.mutateAsync();
            toast(r.detail, r.ok ? "success" : "error");
          }}>Test</Button>
          <Button size="sm" variant="outline" onClick={() => refresh.mutate()} loading={refresh.isPending}><RefreshCw /> Refresh token</Button>
          <Button size="sm" variant="outline" onClick={connect}><Link2 /> Reauthorize</Button>
          <Button size="sm" variant="ghost" onClick={() => confirm("Disconnect and delete stored tokens?") && disconnect.mutate()}><Unplug /> Disconnect</Button>
        </>}
        <Button size="sm" variant="ghost" onClick={() => setSettingsOpen(true)}>Settings</Button>
        <Button size="sm" variant="ghost" onClick={() => patch.mutate({ paused: !a.paused })}>{a.paused ? "Resume" : "Pause"}</Button>
        <Button size="icon" variant="ghost" onClick={() => confirm("Remove this account and its schedules?") && del.mutate()}><Trash2 /></Button>
      </div>
      <Dialog open={settingsOpen} onClose={() => setSettingsOpen(false)} title={`${a.display_name} settings`}>
        <div className="space-y-4">
          <Field label="Publishing mode override">
            <Select value={a.publishing_mode ?? ""} onChange={(e) => patch.mutate({ publishing_mode: e.target.value || null })}>
              <option value="">Inherit from application</option><option value="manual">Manual</option><option value="semi">Semi-automatic</option><option value="auto">Automatic</option>
            </Select>
          </Field>
          {a.platform !== "export" && (
            <Switch checked={a.status === "export_only"} onChange={(x) => patch.mutate({ export_only: x })}
              label="Export only (prepare files for manual upload instead of API publishing)" />
          )}
          {(PLATFORM_SETTINGS[a.platform] ?? []).map((f) => (
            <Field key={f.key} label={f.label} hint={f.hint}>
              {f.options ? (
                <Select value={s[f.key] ?? ""} onChange={(e) => setS({ ...s, [f.key]: e.target.value })}>
                  <option value="">Default</option>
                  {f.options.map((o) => <option key={o}>{o}</option>)}
                </Select>
              ) : (
                <Input value={s[f.key] ?? ""} onChange={(e) => setS({ ...s, [f.key]: e.target.value.trim() })} />
              )}
            </Field>
          ))}
          <Button onClick={async () => { await patch.mutateAsync({ settings: s }); setSettingsOpen(false); }}>Save settings</Button>
        </div>
      </Dialog>
      <Dialog open={tokenOpen} onClose={() => { setTokenOpen(false); setToken(""); }} title="Connect with an access token">
        <form className="space-y-4" onSubmit={async (e) => {
          e.preventDefault();
          try { await tokenConnect.mutateAsync(token); setTokenOpen(false); } finally { setToken(""); }
        }}>
          <p className="text-xs text-muted-foreground">
            Paste a <b>user</b> access token from Meta Graph API Explorer with instagram_basic, instagram_content_publish,
            pages_show_list and business_management. It is exchanged for a 60-day token using the app id/secret from
            Settings → Social platforms, stored encrypted, and never shown again. Set the Instagram user id in this
            account's settings first if several Instagram accounts are linked.
          </p>
          <Field label="Access token">
            <Input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
          </Field>
          <Button type="submit" disabled={token.trim().length < 20} loading={tokenConnect.isPending}>Connect</Button>
        </form>
      </Dialog>
    </Card>
  );
}

export function Accounts() {
  const [params, setParams] = useSearchParams();
  const { toast } = useToast();
  const { data: apps } = useApi<any[]>(["applications"], "/api/applications");
  const { data, isLoading } = useApi<any[]>(["accounts"], "/api/accounts");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<any>({ platform: "youtube", display_name: "", application_id: "", export_only: false });
  const create = useAction((v: any) => api.post("/api/accounts", { ...v, application_id: Number(v.application_id) }), [["accounts"]], "Account added");
  useEffect(() => {
    const o = params.get("oauth");
    if (o === "connected") toast("Account connected", "success");
    if (o === "error") toast(`Connection failed (${params.get("reason")}). See the account card for details.`, "error");
    if (o) setParams({}, { replace: true });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const grouped = (apps ?? []).map((ap) => ({ app: ap, accounts: (data ?? []).filter((a) => a.application_id === ap.id) }));
  return (
    <div>
      <PageHeader title="Social Accounts" description="Official API connections via OAuth. Passwords are never requested or stored."
        actions={<Button onClick={() => setOpen(true)} disabled={!apps?.length}><Plus /> Add account</Button>} />
      {isLoading ? <Spinner /> : !data?.length ? (
        <Empty icon={Users} title="No social accounts">Add an account for an application, then connect it. Configure OAuth clients in Settings → Social platforms first.</Empty>
      ) : (
        <div className="space-y-8">
          {grouped.filter((g) => g.accounts.length).map((g) => (
            <div key={g.app.id}>
              <h2 className="mb-3 text-sm font-semibold text-muted-foreground">{g.app.name}</h2>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{g.accounts.map((a) => <AccountCard key={a.id} a={a} />)}</div>
            </div>
          ))}
        </div>
      )}
      <Dialog open={open} onClose={() => setOpen(false)} title="Add social account">
        <form className="space-y-4" onSubmit={async (e) => { e.preventDefault(); await create.mutateAsync(form); setOpen(false); }}>
          <Field label="Application">
            <Select required value={form.application_id} onChange={(e) => setForm({ ...form, application_id: e.target.value })}>
              <option value="">Select…</option>{apps?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          </Field>
          <Field label="Platform">
            <Select value={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.value })}>
              {Object.entries(PLATFORM_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          </Field>
          <Field label="Display name"><Input required value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder="TaskFlow on TikTok" /></Field>
          {form.platform !== "export" && <Switch checked={form.export_only} onChange={(x) => setForm({ ...form, export_only: x })} label="Export only (no API connection)" />}
          <ErrorBox error={create.error} />
          <Button type="submit" loading={create.isPending}>Add account</Button>
        </form>
      </Dialog>
    </div>
  );
}
