import React, { useState, useRef } from "react";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  Check,
  Search,
  FileText,
  BrainCircuit,
  AlertTriangle,
  Code,
  TerminalSquare,
} from "lucide-react";

export type PlanStepStatus = "pending" | "active" | "success" | "error";

export interface PlanStep {
  id: string;
  title: string;
  content?: React.ReactNode;
  status: PlanStepStatus;
  icon?: React.ReactNode;
  duration?: string;
  defaultExpanded?: boolean;
}

export interface AgentPlanningProps {
  title?: string;
  steps?: PlanStep[];
}

const DEFAULT_STEPS: PlanStep[] = [
  {
    id: "1",
    title: "Analyze request and extract constraints",
    status: "success",
    duration: "0.4s",
    icon: <Search className="w-3.5 h-3.5" />,
    content: (
      <div className="space-y-2 font-mono text-[11px] text-muted-foreground mt-2">
        <div className="flex items-start gap-2 text-emerald-600 dark:text-emerald-400 font-medium">
          <Check className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>Parsed user intent: Build minimalist UI Component</span>
        </div>
        <div className="grid grid-cols-[80px_1fr] gap-1.5 mt-3 bg-secondary/30 p-2.5 rounded-md border border-border/50">
          <span className="text-foreground/50 font-medium">Language:</span>
          <span className="text-foreground">TypeScript, React</span>
          <span className="text-foreground/50 font-medium">Styling:</span>
          <span className="text-foreground">Tailwind CSS v4 (OKLCH variables)</span>
          <span className="text-foreground/50 font-medium">Constraints:</span>
          <span className="text-amber-600 dark:text-amber-400">Single-file, Interactive, No Overlaps</span>
        </div>
      </div>
    ),
  },
  {
    id: "2",
    title: "Search UI knowledge base",
    status: "success",
    duration: "1.2s",
    icon: <FileText className="w-3.5 h-3.5" />,
    content: (
      <div className="space-y-3 font-mono text-[11px] mt-2">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">Executing tool:</span>
          <span className="px-1.5 py-0.5 rounded-md bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20 font-semibold flex items-center gap-1">
            <TerminalSquare className="w-3 h-3" />
            vector_search
          </span>
        </div>
      </div>
    ),
  },
  {
    id: "3",
    title: "Synthesize component logic",
    status: "active",
    duration: "...",
    icon: <BrainCircuit className="w-3.5 h-3.5" />,
    defaultExpanded: true,
    content: (
      <div className="space-y-3 font-mono text-[11px] mt-2">
        <div className="flex items-center gap-2 text-blue-600 dark:text-blue-400 font-medium">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          <span>Generating structured timeline layout...</span>
        </div>
      </div>
    ),
  },
  {
    id: "4",
    title: "Review dependency conflicts",
    status: "error",
    duration: "0.8s",
    icon: <AlertTriangle className="w-3.5 h-3.5" />,
    content: (
      <div className="space-y-2 font-mono text-[11px] mt-2">
        <div className="p-3 rounded-md bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400">
          Warning: Component styling deviation
        </div>
      </div>
    ),
  },
  {
    id: "5",
    title: "Execute final rendering",
    status: "pending",
    icon: <Code className="w-3.5 h-3.5" />,
  },
];

export const AgentPlanning: React.FC<AgentPlanningProps> = ({
  title = "Agent is planning",
  steps = DEFAULT_STEPS,
}) => {
  const [isMainExpanded, setIsMainExpanded] = useState(true);
  const [expandedSteps, setExpandedSteps] = useState<Record<string, boolean>>(
    steps.reduce((acc, step) => {
      acc[step.id] = step.defaultExpanded || false;
      return acc;
    }, {} as Record<string, boolean>)
  );
  const mainContentRef = useRef<HTMLDivElement>(null);

  const toggleStep = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setExpandedSteps((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const hasActive = steps.some((s) => s.status === "active");
  const allSuccess = steps.every((s) => s.status === "success");

  const getStatusColor = (status: PlanStepStatus) => {
    switch (status) {
      case "success":
        return "bg-emerald-100 text-emerald-600 ring-emerald-500/20 dark:bg-emerald-500/20 dark:text-emerald-400";
      case "active":
        return "bg-blue-100 text-blue-600 ring-blue-500/30 dark:bg-blue-500/20 dark:text-blue-400";
      case "error":
        return "bg-rose-100 text-rose-600 ring-rose-500/20 dark:bg-rose-500/20 dark:text-rose-400";
      case "pending":
        return "bg-secondary text-muted-foreground ring-border/50 dark:bg-secondary/50";
    }
  };

  return (
    <div className="w-full max-w-2xl mx-auto my-4 font-sans text-foreground">
      <div className="bg-card border border-border shadow-sm rounded-xl overflow-hidden transition-all duration-300">
        <div
          onClick={() => setIsMainExpanded(!isMainExpanded)}
          className={`flex items-center justify-between px-4 py-3.5 cursor-pointer transition-colors select-none ${
            isMainExpanded ? "bg-secondary/30 border-b border-border/50" : "hover:bg-secondary/30"
          }`}
        >
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-5 h-5">
              {hasActive ? (
                <Loader2 className="w-4 h-4 text-blue-600 dark:text-blue-400 animate-spin" />
              ) : allSuccess ? (
                <Check className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
              ) : (
                <BrainCircuit className="w-4 h-4 text-muted-foreground" />
              )}
            </div>
            <span className="text-[15px] font-semibold text-foreground/90 tracking-tight">{title}</span>
          </div>
          <div className="flex items-center justify-center w-6 h-6 rounded-md hover:bg-secondary text-muted-foreground transition-colors">
            {isMainExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          </div>
        </div>
        <div
          className={`grid transition-all duration-500 ease-in-out bg-card ${
            isMainExpanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
          }`}
        >
          <div className="overflow-hidden">
            <div ref={mainContentRef} className="p-5 flex flex-col">
              {steps.map((step, index) => {
                const isStepExpanded = expandedSteps[step.id];
                const isLast = index === steps.length - 1;
                return (
                  <div
                    key={step.id}
                    className={`relative flex gap-4 ${
                      step.status === "pending" ? "opacity-60 grayscale" : "opacity-100"
                    }`}
                  >
                    {!isLast && (
                      <div className="absolute left-[11px] top-7 bottom-[-10px] w-[2px] bg-border/60 z-0" />
                    )}
                    <div className="relative z-10 flex-none w-6 h-6 mt-0.5">
                      <div
                        className={`flex items-center justify-center w-full h-full rounded-full ring-4 ring-card transition-colors duration-300 ${getStatusColor(
                          step.status
                        )}`}
                      >
                        {step.status === "success" ? (
                          <Check className="w-3.5 h-3.5" />
                        ) : step.status === "active" ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          step.icon || <div className="w-1.5 h-1.5 rounded-full bg-current" />
                        )}
                      </div>
                    </div>
                    <div className="flex-1 pb-6">
                      <div
                        className={`flex items-center justify-between group rounded-md -mx-2 px-2 py-1 transition-colors ${
                          step.content ? "cursor-pointer hover:bg-secondary/50" : ""
                        }`}
                        onClick={(e) => step.content && toggleStep(step.id, e)}
                      >
                        <span
                          className={`text-[14px] tracking-tight transition-colors duration-200 ${
                            step.status === "active"
                              ? "text-foreground font-semibold"
                              : step.status === "error"
                                ? "text-rose-600 dark:text-rose-400 font-semibold"
                                : "text-foreground/80 group-hover:text-foreground font-medium"
                          }`}
                        >
                          {step.title}
                        </span>
                        <div className="flex items-center gap-3">
                          {step.duration && (
                            <span className="text-[11px] font-mono text-muted-foreground tabular-nums">
                              {step.duration}
                            </span>
                          )}
                          {step.content && (
                            <div className="text-muted-foreground/40 group-hover:text-muted-foreground transition-colors">
                              {isStepExpanded ? (
                                <ChevronDown className="w-4 h-4" />
                              ) : (
                                <ChevronRight className="w-4 h-4" />
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                      {step.content && (
                        <div
                          className={`grid transition-all duration-400 ease-in-out ${
                            isStepExpanded ? "grid-rows-[1fr] mt-2 opacity-100" : "grid-rows-[0fr] mt-0 opacity-0"
                          }`}
                        >
                          <div className="overflow-hidden">
                            <div className="pt-1 pb-2">{step.content}</div>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AgentPlanning;
