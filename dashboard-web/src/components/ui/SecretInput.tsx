import { Eye, EyeOff } from "lucide-react";
import { useEffect, useId, useState } from "react";

/** 只切换当前输入框的显示方式，不回显已保存凭据。 */
export function SecretInput({
  label,
  hint,
  value,
  placeholder,
  autoComplete,
  required,
  active,
  onChange,
}: {
  label: string;
  hint: string;
  value: string;
  placeholder: string;
  autoComplete: string;
  required: boolean;
  active: boolean;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!active) setVisible(false);
  }, [active]);
  const revealed = active && visible;
  return (
    <div className="config-secret-field">
      <label htmlFor={id} id={`${id}-hint`}>
        {hint}
      </label>
      <div className="config-secret-input">
        <input
          id={id}
          aria-label={label}
          aria-describedby={`${id}-hint`}
          type={revealed ? "text" : "password"}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          autoComplete={autoComplete}
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          required={required}
        />
        <button
          type="button"
          className="config-secret-toggle"
          aria-label={revealed ? "隐藏密码" : "显示密码"}
          aria-pressed={revealed}
          aria-controls={id}
          disabled={!active}
          onClick={() => setVisible((previous) => !previous)}
        >
          {revealed ? (
            <EyeOff aria-hidden="true" />
          ) : (
            <Eye aria-hidden="true" />
          )}
        </button>
      </div>
    </div>
  );
}
