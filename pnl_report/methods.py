from pnl_report.data_format import DataFormat

import numpy as np
import copy


class PnLCore:

    def __init__(self, data, **kwargs):
        """Init of the Core class of P&L Calculation.
        The data input needs to be a DataFrame, containing core columns:
        - A Quantity Column: The quantity of the trade. + for a Buy, - for a Sell
        - A Side Column: The side of the trade. BUY for a buy, SELL for a Sell
        - A Price Column: The price of the trade
        - A Date Column: When the trade happens

        See bottom for examples

        ---

        You can override default column names via kwargs.

        """
        # Default column names
        self.fmt = DataFormat
        self.cols = {k: v for k, v in {**self.fmt.COLS, **kwargs}.items()}

        # Queues
        self.queue = self.fmt.fmt(data, self.cols).reset_index(drop=True).to_dict(orient='index')
        self._stack = []

        # Data
        self.raw_data = data
        self.pnls = []

    # Properties

    @property
    def pnl(self):
        # return self.pnls[self.cols['pnl_col']].sum()
        return round(sum((v['pnl'] for v in self.pnls)), 2)

    @property
    def side(self):
        """Returns Side of current stack"""
        return self.stack[0][self.cols['side_col']] if self.stack else 'Stack Empty'

    @property
    def stack(self):
        return self._stack

    @property
    def qty(self):
        """Return current stack cumulated position"""
        # return np.abs(sum(el[self.cols['qty_col']] for el in self.stack))
        return np.abs(np.array([el[self.cols['qty_col']] for el in self.stack]).sum())

    @property
    def avg_px(self):
        return np.abs(
            np.array([el[self.cols['qty_col']] * el[self.cols['price_col']] for el in self.stack]).sum()
            / self.qty
        )

    # Stack Functions

    def stack_munched(self, el):
        """Returns current stack as DataFrame, with cumulative position and level of stack being consumed
        by new batch

        First element returned is batches being munched by new processed trade.
        Second element returned is batched not being munched by new processed trade.

        """

        _ = np.cumsum(np.abs(np.array([x[self.cols['qty_col']] for x in self.stack])))
        idx = np.searchsorted(_, abs(el[self.cols['qty_col']]), side='right') + 1

        idx = idx if abs(el[self.cols['qty_col']]) > _[(idx-2):(idx-1)] else idx - 1
        idx = max(idx, 1)

        return self.stack[:idx], self.stack[idx:]

    def to_stack(self, el):
        self.stack.append(el)

    # Quantity Checks

    def same_side(self, el):
        return el[self.cols['side_col']] == self.side

    def same_qty(self, el):
        return abs(el[self.cols['qty_col']]) == self.qty

    def less_qty(self, el):
        return abs(el[self.cols['qty_col']]) < self.qty

    def more_qty(self, el):
        return abs(el[self.cols['qty_col']]) > self.qty

    # Quantity Functions

    def less_qty_func(self, el):
        pass

    def same_qty_func(self, el):
        pass

    def more_qty_func(self, el):
        pass

    # Results Functions

    def compute_pnls(self):
        return self

    def run(self):
        return self


class PnLCalculation(PnLCore):

    def __init__(self, data, **kwargs):
        super().__init__(data=data, **kwargs)

    def less_qty_func(self, el):
        """New batch has less quantity than stacked one"""
        ym, nm = self.stack_munched(el)
        balance = sum(abs(x[self.cols['qty_col']]) for x in ym) - abs(el[self.cols['qty_col']])

        # unwind_dct is a copy of the last element of the batch
        # it has to be processed separately should its quantity not to be unwound entirely
        unwind_dct = copy.deepcopy(ym[-1])
        unwind_dct.update({
            'unwind_date': el[self.cols['date_col']],
            self.cols['unwind_price_col']: el[self.cols['price_col']],
            'unwind_qty': ym[-1][self.cols['qty_col']] + (balance if self.side == 'SELL' else -balance)}
        )

        #
        munched_dct = {
            self.cols['qty_col']: abs(balance) if self.side == 'BUY' else -abs(balance),
            self.cols['price_col']: ym[-1][self.cols['price_col']],
            self.cols['side_col']: ym[-1][self.cols['side_col']],
            self.cols['date_col']: ym[-1][self.cols['date_col']],
        } if balance else {}

        _ = {self.cols['unwind_price_col']: el[self.cols['price_col']], 'unwind_date': el[self.cols['date_col']]}

        for k in ym:
            k.update(_)
            k['unwind_qty'] = k[self.cols['qty_col']]

        self._stack = [munched_dct] + nm if munched_dct else nm
        self.pnls.extend(ym[:-1] + [unwind_dct])

    def same_qty_func(self, el):
        """New batch has same quantity than stacked one"""

        ls = self.stack

        for k in ls:
            k['unwind_qty'] = k[self.cols['qty_col']]
            k['unwind_date'] = el[self.cols['date_col']]
            k[self.cols['unwind_price_col']] = el[self.cols['price_col']]

        self._stack = []
        self.pnls.extend(ls)

    def more_qty_func(self, el):
        """New batch has more quantity than stacked one"""

        ls = self.stack

        for k in ls:
            k['unwind_qty'] = k[self.cols['qty_col']]
            k['unwind_date'] = el[self.cols['date_col']]
            k[self.cols['unwind_price_col']] = el[self.cols['price_col']]

        balance = abs(el[self.cols['qty_col']]) - sum(abs(x[self.cols['qty_col']]) for x in ls)
        el[self.cols['qty_col']] = abs(balance) if el[self.cols['side_col']] == 'BUY' else -abs(balance)

        self._stack = [el]
        self.pnls.extend(ls)

    # Results Functions

    def sanitize(self):
        """
        Removes process-related columns, and ensure quantities have proper sign
        Current design is faster than one for loop, has less overhead.
        """
        if not self.pnls:
            return 0

        _ = [k.update({'unwind_qty': abs(k['unwind_qty'])}) for k in self.pnls if k[self.cols['side_col']] == 'BUY']
        _ = [k.update({'unwind_qty': -abs(k['unwind_qty'])}) for k in self.pnls if k[self.cols['side_col']] == 'SELL']
        _ = [k.pop('_idx', None) for k in self.pnls]

    def compute_pnls(self):
        """Computes the P&L"""

        if not self.pnls:
            return 0

        ls = self.pnls
        _ = [k.update({self.cols['pnl_col']: k['unwind_qty'] * (k[self.cols['unwind_price_col']] -
                                                                k[self.cols['price_col']])}) for k in ls]

        return self

    def run(self):
        """Runs the various steps, going through each batch"""
        conditions_and_functions = [
            (self.has_stack, self.to_stack),
            (self.same_side, self.to_stack),
            (self.less_qty, self.less_qty_func),
            (self.same_qty, self.same_qty_func),
            (self.more_qty, self.more_qty_func)
        ]

        for el in self.queue.values():
            for condition, function in conditions_and_functions:
                if condition(el):
                    function(el)
                    break

        self.sanitize()
        self.compute_pnls()

        return self

    def has_stack(self, el):
        return False if self.stack else True


# ----------------------
# Calculations Methods -
# ----------------------


class FIFO(PnLCalculation):
    """FIRST IN FIRST OUT"""

    def __init__(self, data, **kwargs):
        super().__init__(data=data, **kwargs)

    # Stack Functions

    def to_stack(self, el):
        self._stack.append(el)


class LIFO(PnLCalculation):
    """LAST IN FIRST OUT"""
    def __init__(self, data, **kwargs):
        super().__init__(data, **kwargs)

    # Stack Functions

    def to_stack(self, el):
        self._stack.insert(0, el)


class AVG(PnLCalculation):
    """AVERAGED AS THEY COME"""
    def __init__(self, data, **kwargs):
        super().__init__(data, **kwargs)

    # Properties

    @property
    def stack(self):
        """Overrides to average every new batch into the existing batch"""

        if not self._stack:
            return {}

        ls = copy.deepcopy(self._stack)
        px = [k[self.cols['price_col']] for k in ls]
        qx = [k[self.cols['qty_col']] for k in ls]

        sqx = sum(qx)

        _ = ls[0]
        _[self.cols['price_col']] = sum(a * b for a, b in zip(px, qx)) / sqx
        _[self.cols['qty_col']] = sqx

        return [_]

    # Stack Functions

    def to_stack(self, el):
        self._stack.append(el)

# ----------------------
# Calculations Wrapper -
# ----------------------


class PnLMethods:
    """Wrapper for the various Calculation Methods"""
    METHODS = {'fifo': FIFO, 'lifo': LIFO, 'avg': AVG}
    COLS = DataFormat.COLS

    def __init__(self, data, method='fifo', **kwargs):
        self.calc = self.METHODS[method](data=data, **kwargs)

    def run(self):
        return self.calc.run()
