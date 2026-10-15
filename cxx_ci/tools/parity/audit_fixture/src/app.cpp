#include "app.h"

namespace parity {

int g_counter = 0;   // 全局计数器：命名口径的靶子

int bump(int value)
{
    g_counter += value;
    return g_counter;
}

}  // namespace parity
