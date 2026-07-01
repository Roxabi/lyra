import { Badge } from "@astryxdesign/core/Badge";
import { Banner } from "@astryxdesign/core/Banner";
import { Card } from "@astryxdesign/core/Card";
import { ClickableCard } from "@astryxdesign/core/ClickableCard";
import { Selector } from "@astryxdesign/core/Selector";
import { Skeleton } from "@astryxdesign/core/Skeleton";
import { Stack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { TextArea } from "@astryxdesign/core/TextArea";
import { TextInput } from "@astryxdesign/core/TextInput";
import { List, Moon, Robot, SquaresFour, Sun } from "@phosphor-icons/react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { AgentStatusBadge } from "@/components/AgentStatusBadge";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { AgentIdentity } from "@/components/agents/AgentIdentity";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { ChatEmptyState } from "@/components/chat/ChatEmptyState";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { HarnessPicker } from "@/components/HarnessPicker";
import { PageIntro } from "@/components/layout/PageIntro";
import { ModelPicker } from "@/components/ModelPicker";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { FilterChip } from "@/components/ui/filter-chip";
import {
  ListToolbar,
  ListToolbarControls,
  ListToolbarHeader,
  ListToolbarSearch,
} from "@/components/ui/list-toolbar";
import { PopoverSelect } from "@/components/ui/popover-select";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { Separator } from "@/components/ui/separator";
import { toast } from "@/components/ui/sonner";
import { SortableTableHeader } from "@/components/ui/sortable-table-header";
import { type AgentHealth, MODEL_CATALOG } from "@/lib/api";
import type { HarnessKind } from "@/lib/chats-storage";
import { cn } from "@/lib/utils";

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <Card>
      <Stack gap={4}>
        <Stack gap={1}>
          <Text type="label" as="h3">
            {title}
          </Text>
          {description ? <Text type="supporting">{description}</Text> : null}
        </Stack>
        <Stack gap={4}>{children}</Stack>
      </Stack>
    </Card>
  );
}

function Swatch({ name, className }: { name: string; className: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className={cn("h-12 rounded-md border border-border/60", className)} />
      <span className="text-xs text-muted-foreground">{name}</span>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

const DEMO_MESSAGES = [
  {
    id: "user-1",
    role: "user" as const,
    content: "Bonjour, peux-tu résumer l'état de la flotte ?",
  },
  {
    id: "assistant-1",
    role: "assistant" as const,
    content: "Trois agents sont **en ligne**. Un job `deploy-42` est actif.",
  },
  { id: "error-1", role: "error" as const, content: "Timeout — harness injoignable après 30s." },
];

const ONLINE_HEALTH: AgentHealth = {
  agent: "demo",
  in_roster: true,
  harness: "claude-cli",
  harness_reachable: true,
  online: true,
};

const OFFLINE_HEALTH: AgentHealth = {
  agent: "demo",
  in_roster: true,
  harness: "omp-rpc",
  harness_reachable: false,
  online: false,
};

export function DesignSystemPage() {
  const { t } = useTranslation();
  const [harness, setHarness] = useState<HarnessKind>("claude-cli");
  const [model, setModel] = useState(MODEL_CATALOG["claude-cli"][0]);
  const [popoverValue, setPopoverValue] = useState("a");
  const [textInputValue, setTextInputValue] = useState("");
  const [selectorValue, setSelectorValue] = useState("claude-cli");
  const [composerValue, setComposerValue] = useState("");
  const [listSearch, setListSearch] = useState("");
  const [listView, setListView] = useState<"cards" | "table">("cards");
  const [statusFilter, setStatusFilter] = useState(false);
  const [textareaValue, setTextareaValue] = useState("");

  return (
    <div className="space-y-8 pb-8">
      <PageIntro>
        Catalogue des composants UI du dashboard — tokens, primitives shadcn et composants métier.
      </PageIntro>

      <Section
        title="Couleurs & tokens"
        description="Variables sémantiques mappées depuis le brand kit."
      >
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Swatch name="background" className="bg-background" />
          <Swatch name="card" className="bg-card" />
          <Swatch name="muted" className="bg-muted" />
          <Swatch name="primary" className="bg-primary" />
          <Swatch name="brand" className="bg-brand" />
          <Swatch name="destructive" className="bg-destructive" />
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Swatch name="status-open" className="bg-status-open" />
          <Swatch name="status-closing" className="bg-status-closing" />
          <Swatch name="status-error" className="bg-status-error" />
          <Swatch name="status-idle" className="bg-status-idle" />
        </div>
      </Section>

      <Section title="Typographie">
        <div className="space-y-3">
          <p className="font-[family-name:var(--font-head)] text-2xl font-bold tracking-tight">
            Outfit — titres (font-head)
          </p>
          <p className="text-base text-foreground">Inter — corps de texte (font-body)</p>
          <p className="font-mono text-sm text-muted-foreground">
            JetBrains Mono — job_id, logs, code
          </p>
        </div>
      </Section>

      <Section title="Button" description="Variants, tailles et état loading.">
        <Row label="Variants">
          <Button variant="default">Default</Button>
          <Button variant="brand">Brand</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="outline">Outline</Button>
          <Button variant="ghost">Ghost</Button>
        </Row>
        <Row label="Tailles">
          <Button size="sm">Small</Button>
          <Button size="default">Default</Button>
          <Button size="lg">Large</Button>
          <Button size="icon" aria-label="Icon">
            <Sun className="size-4" />
          </Button>
        </Row>
        <Row label="États">
          <Button disabled>Disabled</Button>
          <Button loading>Loading</Button>
        </Row>
      </Section>

      <Section title="Badge">
        <Row label="Variants">
          <Badge variant="info" label="Default" />
          <Badge variant="neutral" label="Secondary" />
          <Badge variant="success" label="Success" />
          <Badge variant="warning" label="Warning" />
          <Badge variant="error" label="Destructive" />
          <Badge variant="neutral" label="Outline" />
        </Row>
        <Row label="AgentStatusBadge">
          <AgentStatusBadge health={ONLINE_HEALTH} />
          <AgentStatusBadge health={OFFLINE_HEALTH} />
          <AgentStatusBadge health={undefined} />
        </Row>
      </Section>

      <Section
        title="Agents"
        description="Avatar, identité — catalog partagé frontend + mock backend."
      >
        <Row label="AgentAvatar">
          <AgentAvatar agentId="lyra" size="sm" />
          <AgentAvatar agentId="lyra" size="md" />
          <AgentAvatar agentId="aryl" size="md" />
          <AgentAvatar agentId="aryl" size="lg" />
        </Row>
        <Row label="AgentIdentity">
          <AgentIdentity agentId="lyra" subtitle="claude-cli · sonnet" />
          <AgentIdentity agentId="aryl" subtitle="omp-rpc · omp-default" />
        </Row>
      </Section>

      <Section title="Banner" description="Bandeaux d'état — info, warning, error, success.">
        <Banner status="error" title="Message d'erreur critique." />
        <Banner status="warning" title="Avertissement — modifications non enregistrées." />
        <Banner status="info" title="Information contextuelle." />
        <Banner status="success" title="Action réussie." />
      </Section>

      <Section title="Card" description="Variants tone, elevated et interactive.">
        <Card>
          <Stack gap={4}>
            <Stack gap={1}>
              <Text type="label" as="h3">
                CardTitle
              </Text>
              <Text type="supporting">CardDescription — texte secondaire sous le titre.</Text>
            </Stack>
            <p className="text-sm">CardContent — contenu principal de la carte.</p>
          </Stack>
        </Card>
        <ClickableCard
          label="Carte interactive — hover + scale."
          onClick={() => {}}
          className="max-w-xs"
        >
          <p className="text-sm">Carte interactive — hover + scale.</p>
        </ClickableCard>
      </Section>

      <Section
        title="List patterns"
        description="ListToolbar, SegmentedControl, FilterChip, Skeleton, EmptyState — pattern Enishu adapté."
      >
        <ListToolbar>
          <ListToolbarHeader meta="3 agents" />
          <ListToolbarSearch
            value={listSearch}
            onChange={setListSearch}
            placeholder="Rechercher…"
            aria-label="Rechercher"
          />
          <ListToolbarControls
            filters={
              <FilterChip active={statusFilter} onClick={() => setStatusFilter((v) => !v)}>
                running
              </FilterChip>
            }
            view={
              <SegmentedControl
                options={[
                  { value: "cards", label: "Cartes", icon: SquaresFour },
                  { value: "table", label: "Tableau", icon: List },
                ]}
                value={listView}
                onChange={setListView}
                ariaLabel="Vue"
                compact="responsive"
              />
            }
          />
        </ListToolbar>
        <div className="flex flex-wrap gap-3">
          <Skeleton width={160} height={40} />
          <Skeleton width={80} height={24} radius="rounded" />
        </div>
        <EmptyState
          icon={Robot}
          title="Aucun agent"
          hint="Les agents apparaissent ici une fois configurés."
        />
        <table className="w-full max-w-md text-left text-sm">
          <thead>
            <tr className="border-b border-border/50 text-xs">
              <SortableTableHeader
                label="Agent"
                active
                direction="asc"
                onClick={() => {}}
                className="px-2 py-2"
              />
              <th className="py-2 font-medium text-muted-foreground">Statut</th>
            </tr>
          </thead>
        </table>
        <Button type="button" variant="secondary" onClick={() => toast.success("Action réussie")}>
          Déclencher un toast
        </Button>
      </Section>

      <Section title="Formulaires" description="TextInput, Textarea, Selector, PopoverSelect.">
        <div className="grid gap-4 sm:grid-cols-2">
          <TextInput
            label="TextInput"
            value={textInputValue}
            onChange={setTextInputValue}
            placeholder="Placeholder…"
            width="100%"
          />
          <TextInput
            label="TextInput disabled"
            isDisabled
            value=""
            placeholder="Disabled"
            width="100%"
          />
        </div>
        <div className="space-y-2">
          <TextArea
            label="Textarea"
            value={textareaValue}
            onChange={setTextareaValue}
            placeholder="Zone de texte multiligne…"
            rows={3}
          />
        </div>
        <Row label="Selector">
          <Selector
            label="Harness"
            isLabelHidden
            value={selectorValue}
            options={[
              { value: "claude-cli", label: "Clipool" },
              { value: "omp-rpc", label: "OMP" },
            ]}
            onChange={setSelectorValue}
          />
        </Row>
        <Row label="PopoverSelect">
          <PopoverSelect
            label="Option"
            value={popoverValue}
            options={[
              { value: "a", label: "Option A", hint: "avec hint" },
              { value: "b", label: "Option B" },
              { value: "c", label: "Option C", disabled: true },
            ]}
            onChange={setPopoverValue}
          />
        </Row>
      </Section>

      <Section title="Separator">
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">Horizontal</p>
          <Separator />
          <div className="flex h-8 items-center gap-4">
            <span className="text-sm">Gauche</span>
            <Separator orientation="vertical" />
            <span className="text-sm">Droite</span>
          </div>
        </div>
      </Section>

      <Section
        title="Pickers métier"
        description="HarnessPicker et ModelPicker — wrappers autour de PopoverSelect."
      >
        <div className="flex flex-wrap items-start gap-6">
          <HarnessPicker value={harness} onChange={setHarness} dbDefault="claude-cli" />
          <ModelPicker harness={harness} value={model} onChange={setModel} />
          <HarnessPicker value="omp-rpc" onChange={() => {}} disabled />
          <ModelPicker harness="claude-cli" value={model} onChange={() => {}} offline />
        </div>
      </Section>

      <Section title="ThemeToggle">
        <div className="flex items-center gap-3">
          <ThemeToggle />
          <span className="text-sm text-muted-foreground">
            {t("theme.toggle")} — bascule clair / sombre
          </span>
        </div>
      </Section>

      <Section title="Chat" description="MessageBubble, ChatEmptyState, ChatComposer.">
        <div className="space-y-4 rounded-lg border border-border/60 bg-muted/20 p-4">
          {DEMO_MESSAGES.map((msg) => (
            <MessageBubble key={msg.id} message={msg} agentLabel="Lyra" />
          ))}
        </div>
        <Separator />
        <div className="overflow-hidden rounded-lg border border-border/60">
          <div className="h-48">
            <ChatEmptyState agent="Lyra" offline={false} />
          </div>
          <ChatComposer
            value={composerValue}
            disabled={false}
            onChange={setComposerValue}
            onSend={() => setComposerValue("")}
          />
        </div>
      </Section>

      <Section title="Surfaces dashboard" description="Classes utilitaires récurrentes.">
        <div className="space-y-3">
          <div className="dashboard-surface rounded-lg border border-border/60 px-4 py-3 text-sm">
            <code className="font-mono text-xs">dashboard-surface</code> — fond carte produit
          </div>
          <div className="ember-canvas rounded-lg border border-border/60 px-4 py-3 text-sm">
            <code className="font-mono text-xs">ember-canvas</code> — fond shell principal
          </div>
          <div className="flex items-center gap-2 rounded-lg border border-border/60 bg-card px-4 py-3">
            <Moon className="size-4 text-muted-foreground" />
            <p className="text-sm">Bandeau d'alerte — comme sur la vue d'ensemble</p>
          </div>
        </div>
      </Section>
    </div>
  );
}
