/* Copyright 2024 Marimo. All rights reserved. */
"use no memo";

import {
  type Column,
  makeStateUpdater,
  type RowData,
  type Table,
  type TableFeature,
  type Updater,
} from "@tanstack/react-table";
import type { DataType } from "@/core/kernel/messages";
import { logNever } from "@/utils/assertNever";
import {
  prettyEngineeringNumber,
  prettyNumber,
  prettyScientificNumber,
} from "@/utils/numbers";
import type {
  ColumnFormattingOptions,
  ColumnFormattingState,
  ColumnFormattingTableState,
  FormatOption,
} from "./types";

export const ColumnFormattingFeature: TableFeature = {
  // define the column formatting's initial state
  getInitialState: (state): ColumnFormattingTableState => {
    return {
      columnFormatting: {},
      ...state,
    };
  },

  // define the new column formatting's default options
  getDefaultOptions: <TData extends RowData>(
    table: Table<TData>,
  ): ColumnFormattingOptions => {
    return {
      enableColumnFormatting: true,
      onColumnFormattingChange: makeStateUpdater("columnFormatting", table),
    } as ColumnFormattingOptions;
  },

  createColumn: <TData extends RowData>(
    column: Column<TData>,
    table: Table<TData>,
  ) => {
    column.getColumnFormatting = () => {
      return table.getState().columnFormatting[column.id];
    };

    column.getCanFormat = () => {
      return (
        (table.options.enableColumnFormatting &&
          column.columnDef.meta?.dataType !== "unknown" &&
          column.columnDef.meta?.dataType !== undefined) ??
        false
      );
    };

    column.setColumnFormatting = (value) => {
      const safeUpdater: Updater<ColumnFormattingState> = (old) => {
        return {
          ...old,
          [column.id]: value,
        };
      };
      table.options.onColumnFormattingChange?.(safeUpdater);
    };

    // apply column formatting
    column.applyColumnFormatting = (value) => {
      const dataType = column.columnDef.meta?.dataType;
      const format = column.getColumnFormatting?.();
      if (format) {
        return applyFormat(value, { format, dataType });
      }
      return value;
    };
  },
};

const percentFormatter = new Intl.NumberFormat(undefined, {
  style: "percent",
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "short", // 3/4/2024
});

const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "short", // 3/4/2024
  timeStyle: "long", // 3:04:05 PM
  timeZone: "UTC",
});

const timeFormatter = new Intl.DateTimeFormat(undefined, {
  timeStyle: "long", // 3:04:05 PM
  timeZone: "UTC",
});

const integerFormatter = new Intl.NumberFormat(undefined, {
  maximumFractionDigits: 0, // 1,000,000
});

// Apply formatting to a value given a format and data type
export const applyFormat = (
  value: unknown,
  options: {
    format: FormatOption;
    dataType: DataType | undefined;
  },
) => {
  const { format, dataType } = options;
  // If the value is null, return an empty string
  if (value === null || value === undefined || value === "") {
    return "";
  }
  // Handle date, number, string and boolean formatting
  switch (dataType) {
    case "time":
      // Do nothing
      return value;
    case "datetime":
    case "date": {
      const date = new Date(value as string);
      switch (format) {
        case "Date":
          return dateFormatter.format(date);
        case "Datetime":
          return dateTimeFormatter.format(date);
        case "Time":
          return timeFormatter.format(date);
        default:
          return value;
      }
    }
    case "integer":
    case "number": {
      const num = Number.parseFloat(value as string);
      switch (format) {
        case "Auto":
          return prettyNumber(num);
        case "Percent":
          return percentFormatter.format(num);
        case "Scientific":
          return prettyScientificNumber(num, { shouldRound: true });
        case "Engineering":
          return prettyEngineeringNumber(num);
        case "Integer":
          return integerFormatter.format(num);
        default:
          return value;
      }
    }
    case "string":
      switch (format) {
        case "Uppercase":
          return (value as string).toUpperCase();
        case "Lowercase":
          return (value as string).toLowerCase();
        case "Capitalize":
          return (
            (value as string).charAt(0).toUpperCase() +
            (value as string).slice(1)
          );
        case "Title":
          return (value as string)
            .split(" ")
            .map(
              (word) =>
                word.charAt(0).toUpperCase() + word.slice(1).toLowerCase(),
            )
            .join(" ");
        default:
          return value;
      }
    case "boolean":
      switch (format) {
        case "Yes/No":
          return (value as boolean) ? "Yes" : "No";
        case "On/Off":
          return (value as boolean) ? "On" : "Off";
        default:
          return value;
      }
    case undefined:
    case "unknown":
      return value;
    default:
      logNever(dataType);
      return value;
  }
};

export function formattingExample(
  format: FormatOption,
): string | number | undefined | null {
  switch (format) {
    case "Date":
      return String(
        applyFormat(new Date(), { format: "Date", dataType: "date" }),
      );
    case "Datetime":
      return String(
        applyFormat(new Date(), {
          format: "Datetime",
          dataType: "date",
        }),
      );
    case "Time":
      return String(
        applyFormat(new Date(), { format: "Time", dataType: "date" }),
      );
    case "Percent":
      return String(
        applyFormat(0.1234, { format: "Percent", dataType: "number" }),
      );
    case "Scientific":
      return String(
        applyFormat(12_345_678_910, {
          format: "Scientific",
          dataType: "number",
        }),
      );
    case "Engineering":
      return String(
        applyFormat(12_345_678_910, {
          format: "Engineering",
          dataType: "number",
        }),
      );
    case "Integer":
      return String(
        applyFormat(1234.567, { format: "Integer", dataType: "number" }),
      );
    case "Auto":
      return String(
        applyFormat(1234.567, { format: "Auto", dataType: "number" }),
      );
    default:
      return null;
  }
}
