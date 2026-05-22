# Module Formula Index

Этот файл фиксирует, что каждая спецификация модуля содержит:

- `Metric formulas / calculation rules`
- `Forbidden actions`
- строгие input/output contracts
- контуры обновления
- stores read/write policy

Проверка сборки должна падать, если в любом файле `modules/*.md` отсутствует раздел `Forbidden actions`.
