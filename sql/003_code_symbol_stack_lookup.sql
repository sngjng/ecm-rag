-- Java stack trace의 package/class/method를 디컴파일 심볼과 빠르게 연결한다.
-- 기존 DB에 반복 적용해도 안전하다.
CREATE INDEX IF NOT EXISTS idx_symbols_stack_lookup
  ON rag.code_symbols(package_name, class_name, symbol_name);
