/* Copyright 2024 Marimo. All rights reserved. */

import { InfoIcon, type LucideProps } from "lucide-react";
import { ReadonlyCode } from "@/components/editor/code/readonly-python-code";
import {
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/utils/cn";

export const Title: React.FC<{ text: string; tooltip?: string }> = ({
  text,
  tooltip,
}) => {
  return (
    <h2 className="font-semibold my-0 flex items-center gap-1">
      {text}
      {tooltip && (
        <Tooltip content={tooltip}>
          <InfoIcon className="w-3 h-3 mt-0.5" />
        </Tooltip>
      )}
    </h2>
  );
};

export const TabContainer: React.FC<{
  className?: string;
  children: React.ReactNode;
}> = ({ children, className }) => {
  return <div className={cn("flex flex-col gap-2", className)}>{children}</div>;
};

export const FormSectionHorizontalRule: React.FC<{
  className?: string;
}> = ({ className }) => {
  return <hr className={cn("my-1", className)} />;
};

export const FieldSection: React.FC<{
  children: React.ReactNode;
  className?: string;
}> = ({ children, className }) => {
  return (
    <section className={cn("flex flex-col gap-1.5", className)}>
      {children}
    </section>
  );
};

export const IconWithText: React.FC<{
  Icon: React.ForwardRefExoticComponent<
    Omit<LucideProps, "ref"> & React.RefAttributes<SVGSVGElement>
  >;
  text: string;
}> = ({ Icon, text }) => {
  return (
    <div className="flex items-center">
      <Icon className="w-3 h-3 mr-2" />
      <span>{text}</span>
    </div>
  );
};

export const AccordionFormTrigger: React.FC<{
  children: React.ReactNode;
  className?: string;
}> = ({ children, className }) => {
  return (
    <AccordionTrigger className={cn("py-1", className)}>
      {children}
    </AccordionTrigger>
  );
};

export const AccordionFormItem: React.FC<{
  children: React.ReactNode;
  value: string;
}> = ({ children, value }) => {
  return (
    <AccordionItem value={value} className="border-none">
      {children}
    </AccordionItem>
  );
};

export const AccordionFormContent: React.FC<{
  children: React.ReactNode;
  wrapperClassName?: string;
}> = ({ children, wrapperClassName }) => {
  return (
    <AccordionContent
      wrapperClassName={cn("pb-2 flex flex-col gap-2", wrapperClassName)}
    >
      {children}
    </AccordionContent>
  );
};

export const CodeSnippet: React.FC<{
  code: string;
  insertNewCell?: boolean;
  language?: "python" | "sql";
}> = ({ code, insertNewCell, language }) => {
  return (
    <ReadonlyCode
      minHeight="330px"
      maxHeight="330px"
      code={code}
      language={language}
      showHideCode={false}
      showCopyCode={false}
      insertNewCell={insertNewCell}
    />
  );
};
