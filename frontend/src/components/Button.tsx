import type { ReactNode } from "react";
import styles from "./Button.module.css";

interface ButtonProps {
  children: ReactNode;
  onClick?: () => void;
  primary?: boolean;
  secondary?: boolean;
  ghost?: boolean;
  full?: boolean;
  size?: "sm" | "md" | "lg";
  disabled?: boolean;
}

export function Button({
  children,
  onClick,
  primary,
  secondary,
  ghost,
  full,
  size = "md",
  disabled,
}: ButtonProps) {
  const variantClass = primary
    ? styles.primary
    : secondary
    ? styles.secondary
    : ghost
    ? styles.ghost
    : styles.default;

  const sizeClass = styles[size];

  const classNames = [
    styles.btn,
    variantClass,
    sizeClass,
    full ? styles.full : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      // ⚠️ 显式 type="button"：不写的话 HTML 默认是 submit，一旦哪天这颗按钮落进 <form> 里，
      // 点它会变成提交表单（整页刷新），而这里根本没人想提交任何东西。
      type="button"
      className={classNames}
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
