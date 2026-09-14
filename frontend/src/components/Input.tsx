import styles from "./Input.module.css";

interface InputProps {
  type?: string;
  value: string;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  placeholder?: string;
  autoFocus?: boolean;
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
}

export function Input({ type = "text", value, onChange, placeholder, autoFocus, onKeyDown }: InputProps) {
  return (
    <input
      className={styles.input}
      type={type}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      autoFocus={autoFocus}
      onKeyDown={onKeyDown}
    />
  );
}
