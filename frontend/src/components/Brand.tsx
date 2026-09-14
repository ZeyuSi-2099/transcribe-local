import { semantic, fonts } from "../styles/tokens";

interface BrandProps {
  size?: number;
}

export function Brand({ size = 22 }: BrandProps) {
  return (
    <div style={{ fontFamily: fonts.serif, fontSize: size, fontWeight: 500, letterSpacing: -0.4, color: semantic.text.primary }}>
      Transcribe<span style={{ color: semantic.accent.brand }}>.</span>
    </div>
  );
}
