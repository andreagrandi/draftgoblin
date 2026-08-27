pragma ComponentBehavior: Bound

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: root
    objectName: "navigationRail"

    required property string currentSurface
    required property bool compact
    signal surfaceRequested(string surface)
    signal aboutRequested(var opener)
    signal privacyRequested(var opener)

    color: Theme.surfaceLow
    implicitWidth: compact ? 116 : 176

    ButtonGroup {
        id: navigationGroup
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        ColumnLayout {
            Layout.fillWidth: true
            visible: !root.compact
            spacing: 6

            Label {
                Layout.fillWidth: true
                text: "DRAFTOMEN"
                color: Theme.primary
                font.pixelSize: Theme.textPixelSize(14)
                font.bold: true
                font.letterSpacing: 1.2
                horizontalAlignment: Text.AlignHCenter
            }

            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: 144
                Layout.preferredHeight: 144
                color: Theme.background
                border.color: Theme.outline
                border.width: 1
                radius: Theme.radius
                clip: true

                Image {
                    objectName: "draftomenLogo"
                    anchors.fill: parent
                    source: "../assets/draftomen_logo.png"
                    sourceSize.width: 288
                    sourceSize.height: 288
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    mipmap: true
                    Accessible.name: "Draft Omen logo"
                }
            }
        }

        Repeater {
            model: [
                { key: "live", label: "Live Draft", shortLabel: "Draft", icon: "gavel" },
                { key: "build", label: "Deck Build", shortLabel: "Build", icon: "style" },
                { key: "backtest", label: "Backtest", shortLabel: "Backtest", icon: "history" }
            ]

            delegate: DimensionalButton {
                id: navigationButton
                required property var modelData

                Layout.fillWidth: true
                Layout.preferredHeight: Theme.targetHeight
                text: root.compact
                    ? navigationButton.modelData.shortLabel
                    : navigationButton.modelData.label
                checkable: true
                checked: root.currentSurface === navigationButton.modelData.key
                accented: navigationButton.checked
                ButtonGroup.group: navigationGroup
                Accessible.name: navigationButton.modelData.label
                Accessible.description: "Open the " + navigationButton.modelData.label + " surface."
                onClicked: root.surfaceRequested(navigationButton.modelData.key)

                contentItem: RowLayout {
                    anchors.fill: parent
                    anchors.topMargin: navigationButton.down ? Theme.controlPressOffset : 0
                    anchors.leftMargin: root.compact ? 6 : 10
                    anchors.rightMargin: root.compact ? 6 : 10
                    spacing: root.compact ? 6 : 10

                    Canvas {
                        id: navigationIcon
                        objectName: navigationButton.modelData.key + "NavigationIcon"
                        Layout.preferredWidth: 24
                        Layout.preferredHeight: 24
                        Layout.alignment: Qt.AlignVCenter
                        implicitWidth: 24
                        implicitHeight: 24
                        property string iconName: navigationButton.modelData.icon
                        property color iconColor: navigationButton.contentColor

                        onIconNameChanged: requestPaint()
                        onIconColorChanged: requestPaint()
                        Component.onCompleted: requestPaint()

                        onPaint: {
                            var context = getContext("2d")
                            context.clearRect(0, 0, width, height)
                            context.strokeStyle = iconColor
                            context.fillStyle = iconColor
                            context.lineWidth = 2
                            context.lineCap = "round"
                            context.lineJoin = "round"

                            if (iconName === "gavel") {
                                context.save()
                                context.translate(12, 11)
                                context.rotate(-Math.PI / 4)
                                context.fillRect(-4, -9, 8, 9)
                                context.fillRect(-1.5, 0, 3, 10)
                                context.restore()
                                context.fillRect(2, 20, 20, 2)
                            } else if (iconName === "style") {
                                context.strokeRect(4, 3, 16, 13)
                                context.strokeRect(2, 7, 16, 13)
                                context.beginPath()
                                context.moveTo(7, 11)
                                context.lineTo(15, 11)
                                context.stroke()
                            } else if (iconName === "history") {
                                context.beginPath()
                                context.arc(12, 12, 8, -Math.PI * 0.78, Math.PI * 1.64)
                                context.stroke()
                                context.beginPath()
                                context.moveTo(4, 4)
                                context.lineTo(4, 9)
                                context.lineTo(9, 7)
                                context.fill()
                                context.beginPath()
                                context.moveTo(12, 7)
                                context.lineTo(12, 12)
                                context.lineTo(16, 14)
                                context.stroke()
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: navigationButton.text
                        color: navigationButton.contentColor
                        font.bold: navigationButton.checked
                        verticalAlignment: Text.AlignVCenter
                        horizontalAlignment: Text.AlignLeft
                    }
                }
            }
        }

        Item {
            Layout.fillHeight: true
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6

            DimensionalButton {
                id: aboutButton
                objectName: "aboutLink"
                Layout.fillWidth: true
                text: "About"
                accented: true
                accentColor: Theme.primary
                accentContentColor: Theme.primaryContent
                Accessible.name: "Open About dialog"
                Accessible.description: "Show Draft Omen information and project website."
                onClicked: root.aboutRequested(aboutButton)
            }

            DimensionalButton {
                id: privacyButton
                objectName: "privacyLink"
                Layout.fillWidth: true
                text: "Privacy"
                accented: true
                accentColor: Theme.secondary
                accentContentColor: Theme.secondaryContent
                activeFocusOnTab: true
                focusPolicy: Qt.StrongFocus
                Accessible.role: Accessible.Button
                Accessible.name: "Open Privacy dialog"
                Accessible.description: "Show how Draft Omen handles your data."
                onClicked: root.privacyRequested(privacyButton)
            }

            Label {
                Layout.fillWidth: true
                text: root.compact ? "Read only" : "Arena integration · Read only"
                color: Theme.textMuted
                font.pixelSize: Theme.textPixelSize(10)
                wrapMode: Text.WordWrap
            }
        }
    }

    Rectangle {
        anchors.right: parent.right
        width: 1
        height: parent.height
        color: Theme.outline
    }
}

