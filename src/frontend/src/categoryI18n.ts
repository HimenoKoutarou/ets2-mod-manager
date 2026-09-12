import type { Language } from "./types";

export const categoryCopy = {
  zh_CN: {
    create: "新建分类", rename: "重命名分类", delete: "删除分类", uncategorized: "未分类",
    name: "分类名称", confirm: "确认", cancel: "取消", actions: "分类操作",
    deleteConfirm: "删除此分类？其中的 Mod 将变为未分类，不会删除文件或改变启用状态。",
    scope: "操作范围", selection: "已选 Mod", filtered: "当前筛选", category: "整个分类",
    select: "选择", selectVisible: "选择当前列表", clear: "清除选择",
    enable: "启用", disable: "禁用", invert: "反选启用状态", assign: "归入分类", steps: "移动步数",
    categoryToggle: "启用或禁用整个分类", categoryAssign: "将选中 Mod 归入此分类",
    category_invalid_name: "分类名称须为 1–80 个字符，不能包含控制字符。",
    category_exists: "已有同名分类。", category_missing: "分类已不存在，请刷新后重试。",
    category_mod_missing: "所选 Mod 不在当前索引中，请扫描后重试。",
    importWarning: "旧分类导入失败，原文件未修改；可继续管理 Mod。",
  },
  en_US: {
    create: "New category", rename: "Rename category", delete: "Delete category", uncategorized: "Uncategorized",
    name: "Category name", confirm: "Confirm", cancel: "Cancel", actions: "Category actions",
    deleteConfirm: "Delete this category? Its Mods become uncategorized. Files and enabled states are unchanged.",
    scope: "Action scope", selection: "Selected Mods", filtered: "Current filter", category: "Whole category",
    select: "Select", selectVisible: "Select visible list", clear: "Clear selection",
    enable: "Enable", disable: "Disable", invert: "Invert enabled state", assign: "Assign category", steps: "Move steps",
    categoryToggle: "Enable or disable whole category", categoryAssign: "Assign selected Mods here",
    category_invalid_name: "Use 1–80 characters without control characters.",
    category_exists: "A category with this name already exists.", category_missing: "Category no longer exists. Refresh and retry.",
    category_mod_missing: "A selected Mod is missing from the index. Scan and retry.",
    importWarning: "Legacy category import failed. Originals are unchanged; Mod management remains available.",
  },
  ru_RU: {
    create: "Новая категория", rename: "Переименовать", delete: "Удалить категорию", uncategorized: "Без категории",
    name: "Название категории", confirm: "Подтвердить", cancel: "Отмена", actions: "Действия с категорией",
    deleteConfirm: "Удалить категорию? Моды останутся без категории. Файлы и состояние включения не изменятся.",
    scope: "Область действия", selection: "Выбранные моды", filtered: "Текущий фильтр", category: "Вся категория",
    select: "Выбрать", selectVisible: "Выбрать текущий список", clear: "Снять выделение",
    enable: "Включить", disable: "Отключить", invert: "Инвертировать состояние", assign: "Назначить категорию", steps: "Шаг перемещения",
    categoryToggle: "Включить или отключить всю категорию", categoryAssign: "Назначить выбранные моды сюда",
    category_invalid_name: "От 1 до 80 символов без управляющих символов.",
    category_exists: "Категория с таким названием уже существует.", category_missing: "Категория больше не существует. Обновите список.",
    category_mod_missing: "Выбранный мод отсутствует в индексе. Повторите сканирование.",
    importWarning: "Не удалось импортировать категории. Исходные файлы не изменены; управление модами доступно.",
  },
} satisfies Record<Language, Record<string, string>>;

export function categoryError(error: string, language: Language): string {
  const copy = categoryCopy[language];
  return error in copy ? copy[error as keyof typeof copy] : error;
}
