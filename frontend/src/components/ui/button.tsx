import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva("inline-flex items-center justify-center gap-2 rounded-lg text-sm font-semibold transition-colors disabled:pointer-events-none disabled:opacity-60", {
  variants: {
    variant: {
      default: "bg-ink text-white hover:bg-slate-800",
      outline: "border border-slate-200 bg-white text-ink hover:bg-slate-50",
    },
    size: { default: "px-4 py-2.5", sm: "px-3 py-2" },
  },
  defaultVariants: { variant: "default", size: "default" },
});

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, asChild = false, ...props },
  ref,
) {
  const Component = asChild ? Slot : "button";
  return <Component ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
});
