import { useState } from "react";
import { toast } from "sonner";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Toggle } from "@/components/ui/toggle";
import { JobStatusBadge } from "@/shared/components/job-status-badge";
import {
  ListToolbar,
  ListToolbarControls,
  ListToolbarHeader,
  ListToolbarSearch,
} from "@/shared/components/list-toolbar";
import { PageHeader } from "@/shared/components/page-header";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-4">
      <h2 className="text-lg font-medium">{title}</h2>
      {children}
    </section>
  );
}

export function DesignSystemPage() {
  const [checked, setChecked] = useState(true);

  return (
    <div className="space-y-10">
      <PageHeader
        title="Design system"
        description="shadcn/Base UI + thème Forge natif (variables sémantiques --primary, surfaces Obsidian)."
      />

      <Section title="Forge theme (shadcn native)">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Semantic tokens</CardTitle>
            <CardDescription>
              Mappés depuis <code className="rounded bg-muted px-1">brand/tokens/</code> via{" "}
              <code className="rounded bg-muted px-1">src/theme/forge.css</code> — pas de couche
              parallèle <code className="rounded bg-muted px-1">--brand-*</code>.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-3">
            <div className="flex size-16 flex-col items-center justify-center rounded-lg bg-primary text-xs font-medium text-primary-foreground">
              primary
            </div>
            <div className="flex size-16 flex-col items-center justify-center rounded-lg bg-background text-xs text-foreground ring-1 ring-border">
              bg
            </div>
            <div className="flex size-16 flex-col items-center justify-center rounded-lg bg-card text-xs text-card-foreground ring-1 ring-border">
              card
            </div>
            <div className="flex size-16 flex-col items-center justify-center rounded-lg bg-muted text-xs text-muted-foreground">
              muted
            </div>
            <div className="flex size-16 flex-col items-center justify-center rounded-lg bg-accent text-xs text-accent-foreground">
              accent
            </div>
          </CardContent>
        </Card>
        <div className="flex flex-wrap gap-2">
          <JobStatusBadge status="open" />
          <JobStatusBadge status="closing" />
          <JobStatusBadge status="failed" />
        </div>
      </Section>

      <Section title="Buttons">
        <div className="flex flex-wrap gap-2">
          <Button>Default</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="outline">Outline</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="destructive">Destructive</Button>
          <Button variant="link">Link</Button>
        </div>
      </Section>

      <Section title="Badges">
        <div className="flex flex-wrap gap-2">
          <Badge>Default</Badge>
          <Badge variant="secondary">Secondary</Badge>
          <Badge variant="outline">Outline</Badge>
          <Badge variant="destructive">Destructive</Badge>
        </div>
      </Section>

      <Section title="Form controls">
        <Card className="max-w-md">
          <CardHeader>
            <CardTitle>Sample form</CardTitle>
            <CardDescription>Input, label, checkbox, switch, textarea</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="name">Name</Label>
              <Input id="name" placeholder="Agent name" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="notes">Notes</Label>
              <Textarea id="notes" placeholder="Optional context…" rows={3} />
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="active"
                checked={checked}
                onCheckedChange={(v) => setChecked(v === true)}
              />
              <Label htmlFor="active">Active</Label>
            </div>
            <div className="flex items-center gap-2">
              <Switch id="notify" defaultChecked />
              <Label htmlFor="notify">Notifications</Label>
            </div>
          </CardContent>
        </Card>
      </Section>

      <Section title="Feedback">
        <Alert>
          <AlertTitle>Heads up</AlertTitle>
          <AlertDescription>
            Alert, toast, skeleton — états de chargement et messages.
          </AlertDescription>
        </Alert>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            onClick={() => toast.success("Toast OK — sonner branché sur ThemeProvider")}
          >
            Show toast
          </Button>
          <div className="flex items-center gap-2">
            <Skeleton className="h-8 w-24" />
            <Skeleton className="h-8 w-32" />
          </div>
        </div>
      </Section>

      <Section title="Overlays">
        <div className="flex flex-wrap items-center gap-2">
          <Dialog>
            <DialogTrigger render={<Button variant="outline" />}>Open dialog</DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Dialog</DialogTitle>
                <DialogDescription>Base UI dialog — zéro dépendance v1.</DialogDescription>
              </DialogHeader>
            </DialogContent>
          </Dialog>
          <Toggle aria-label="Toggle bold">B</Toggle>
        </div>
      </Section>

      <Section title="List toolbar (shared composite)">
        <ListToolbar>
          <ListToolbarHeader meta="42 items" actions={<Button size="sm">Action</Button>} />
          <ListToolbarSearch
            value=""
            onChange={() => {}}
            placeholder="Search…"
            aria-label="Search"
          />
          <ListToolbarControls
            filters={
              <Button variant="outline" size="sm">
                Filter
              </Button>
            }
            view={
              <Button variant="outline" size="sm">
                View
              </Button>
            }
          />
        </ListToolbar>
      </Section>

      <Section title="Tabs">
        <Tabs defaultValue="a" className="max-w-md">
          <TabsList>
            <TabsTrigger value="a">Tab A</TabsTrigger>
            <TabsTrigger value="b">Tab B</TabsTrigger>
          </TabsList>
          <TabsContent value="a" className="text-sm text-muted-foreground">
            Contenu onglet A
          </TabsContent>
          <TabsContent value="b" className="text-sm text-muted-foreground">
            Contenu onglet B
          </TabsContent>
        </Tabs>
      </Section>

      <Separator />

      <p className="text-xs text-muted-foreground">
        Polices : Inter (body) · Outfit (headings). Composants via{" "}
        <code className="rounded bg-muted px-1 py-0.5">shadcn add</code>.
      </p>
    </div>
  );
}
