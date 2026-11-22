/**
 * Drag a lineup row onto another spot to swap them: a thin layer over the Move buttons. The picked spot lives in React
 * state (the same `source` the Move button sets), never in dataTransfer, so a drop and a tap-move post the same lineup.
 * Touch screens don't fire HTML5 drag events; there the Move button is the way.
 */
import type { DragEvent, HTMLAttributes } from 'react';
import { canSwap, type LineupSpot } from './teamLineup';

export interface LineupDragHandlers {
  /** The spot being moved, if any. */
  source: LineupSpot | null;
  isDisabled: boolean;
  /** Picks a spot up (or puts it down with null) without moving anyone. */
  onPick: (spot: LineupSpot | null) => void;
  /** Moves the picked player into `spot` (the Move button's second press). */
  onMove: (spot: LineupSpot) => void;
}

// One drag at a time on the page: the pick waiting for the next frame, if any, and whether it has happened.
let pendingPick = 0;
let hasPicked = false;

/** The drag attributes for one roster row; none when the lineup can't be changed. */
export function lineupDragProps(spot: LineupSpot, editing: LineupDragHandlers | undefined): HTMLAttributes<HTMLTableRowElement> {
  if (!editing || editing.isDisabled) return {};
  const { source } = editing;
  const isLegal = source !== null && canSwap(source, spot);
  return {
    draggable: spot.row !== null,
    onDragStart: (event: DragEvent) => {
      // Firefox only starts a drag that carries data; jsdom has no dataTransfer at all.
      event.dataTransfer?.setData('text/plain', spot.row?.player.name ?? '');
      if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
      // Next frame: Chrome aborts a drag whose source changes in the dragstart handler itself (the dim/target classes).
      cancelAnimationFrame(pendingPick);
      hasPicked = false;
      pendingPick = requestAnimationFrame(() => {
        hasPicked = true;
        editing.onPick(spot);
      });
    },
    onDragOver: (event: DragEvent) => {
      if (!isLegal) return;            // not cancelling dragover is what tells the browser "no drop here"
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    },
    onDrop: (event: DragEvent) => {
      event.preventDefault();
      if (isLegal) editing.onMove(spot);
    },
    onDragEnd: () => {
      cancelAnimationFrame(pendingPick);
      // Only put down what this drag picked up: a drag aborted before its frame keeps a tap-Move selection.
      if (hasPicked) editing.onPick(null);
      hasPicked = false;
    },
  };
}
