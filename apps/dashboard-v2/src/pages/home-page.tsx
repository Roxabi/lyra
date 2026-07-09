import { Link } from "@tanstack/react-router";
import { Layers, Package } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/shared/components/page-header";

const CORE_LAYERS = [
  { name: "app/", desc: "nav, providers, shell-title, query defaults" },
  { name: "shared/api/", desc: "BFF client, operator auth, bffFetch" },
  { name: "shared/components/", desc: "PageHeader, EmptyState, ListToolbar" },
  { name: "shared/lib/", desc: "sort, compare — pure utilities" },
  { name: "components/app-shell/", desc: "sidebar + header + layout variants" },
  { name: "components/ui/", desc: "shadcn/Base UI — CLI only" },
  { name: "features/*", desc: "domain pages — next phase" },
] as const;

export function HomePage() {
  return (
    <div className="space-y-8">
      <PageHeader
        title="Overview"
        description="Noyau v2 en place — features branchées page par page ensuite."
        actions={
          <Button variant="outline" size="sm" render={<Link to="/design-system" />}>
            Design system
          </Button>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Package className="size-4" aria-hidden />
              Packages
            </CardTitle>
            <CardDescription>Aucun package monorepo requis pour le noyau</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1 text-sm text-muted-foreground">
            <p>
              Tout vit dans <code className="rounded bg-muted px-1">apps/dashboard-v2</code>
            </p>
            <p>
              BFF via proxy <code className="rounded bg-muted px-1">/api</code> ou dev-mock
            </p>
            <p>
              Brand Forge (<code className="rounded bg-muted px-1">@roxabi-factory/shared</code>) —
              phase ultérieure
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Layers className="size-4" aria-hidden />
              Architecture
            </CardTitle>
            <CardDescription>Dépendances autorisées</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1.5 text-sm text-muted-foreground">
              {CORE_LAYERS.map((layer) => (
                <li key={layer.name}>
                  <code className="rounded bg-muted px-1 text-foreground">{layer.name}</code> —{" "}
                  {layer.desc}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
