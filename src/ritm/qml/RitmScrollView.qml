import QtQuick.Controls

ScrollView {
    id: view
    required property var appearance

    clip: true
    contentWidth: availableWidth
    rightPadding: appearance.usesSharedStyle
        ? ScrollBar.vertical.implicitWidth + appearance.dp(6) : 0
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ScrollBar.vertical.palette.mid: appearance.colors.scrollbar ?? view.palette.mid
    ScrollBar.vertical.palette.dark: appearance.colors.scrollbar ?? view.palette.dark
}
